"""The recommendation engine.

Scoring, in order of weight:

1. specific interests   (+10 each match)
2. broad topic          (+4)
3. preferred depth      (+3 exact, +1 when the reader asked for a mix)
4. reader feedback      (+5 "more like this", -8 "less like this")

Variety is applied as small penalties while greedily picking the three pieces, so
it can break a near-tie between comparable items but never outweighs an interest
match. Ties are broken by content id, which makes selection deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.content import INTEREST_LABELS, TOPIC_LABELS
from app.models import ContentItem, Edition, EditionItem, Feedback

INTEREST_WEIGHT = 10.0
TOPIC_WEIGHT = 4.0
DEPTH_WEIGHT = 3.0
MIX_DEPTH_WEIGHT = 1.0
FEEDBACK_MORE = 5.0
FEEDBACK_LESS = -8.0

SAME_FORMAT_PENALTY = 1.5
SAME_TOPIC_PENALTY = 2.0
SAME_CREATOR_PENALTY = 6.0
SAME_INTEREST_PENALTY = 1.5

EDITION_SIZE = 3


@dataclass
class Profile:
    topics: list[str]
    interests: list[str]
    depth: str = "mix"

    @classmethod
    def from_dict(cls, data: dict) -> Profile:
        return cls(
            topics=list(data.get("topics") or []),
            interests=list(data.get("interests") or []),
            depth=data.get("depth") or "mix",
        )

    def to_dict(self) -> dict:
        return {"topics": list(self.topics), "interests": list(self.interests), "depth": self.depth}


@dataclass
class Selection:
    content: ContentItem
    reason: str
    is_revisit: bool
    score: float


def _depth_score(item: ContentItem, depth: str) -> float:
    if depth == "mix":
        return MIX_DEPTH_WEIGHT
    return DEPTH_WEIGHT if item.depth == depth else 0.0


def base_score(item: ContentItem, profile: Profile, feedback: dict[str, str] | None = None) -> float:
    matched = set(item.interests or []) & set(profile.interests)
    score = INTEREST_WEIGHT * len(matched)
    if item.topic in profile.topics:
        score += TOPIC_WEIGHT
    score += _depth_score(item, profile.depth)
    signal = (feedback or {}).get(item.id)
    if signal == "more":
        score += FEEDBACK_MORE
    elif signal == "less":
        score += FEEDBACK_LESS
    return score


def explain(item: ContentItem, profile: Profile, is_revisit: bool = False) -> str:
    matched = [i for i in (item.interests or []) if i in profile.interests]
    parts: list[str] = []
    if matched:
        names = [INTEREST_LABELS.get(i, i) for i in matched]
        joined = names[0] if len(names) == 1 else " and ".join([", ".join(names[:-1]), names[-1]])
        parts.append(f"You asked for {joined}")
    elif item.topic in profile.topics:
        parts.append(f"You chose {TOPIC_LABELS.get(item.topic, item.topic)}")
    else:
        parts.append(f"Close to your {TOPIC_LABELS.get(item.topic, item.topic)} choices")

    if profile.depth == "accessible" and item.depth == "accessible":
        parts.append("and this is an accessible introduction")
    elif profile.depth == "deep" and item.depth == "deep":
        parts.append("and this is a deeper exploration")
    elif profile.depth == "mix":
        parts.append(
            "and this is "
            + ("an accessible introduction" if item.depth == "accessible" else "a deeper exploration")
            + ", part of the mix you asked for"
        )
    else:
        wanted = "accessible introductions" if profile.depth == "accessible" else "deeper explorations"
        got = "an accessible introduction" if item.depth == "accessible" else "a deeper exploration"
        parts.append(f"and although you prefer {wanted}, this one is {got} worth the detour")

    sentence = " ".join(parts) + "."
    if is_revisit:
        sentence += (
            " A revisit: you have seen this in an earlier edition, and it is the best match"
            " left in the library today."
        )
    return sentence


def delivered_history(db: Session, subscriber_id: str) -> dict[str, datetime]:
    """content_id -> most recent time it appeared in one of this reader's editions."""
    rows = db.execute(
        select(EditionItem.content_id, Edition.created_at)
        .join(Edition, Edition.id == EditionItem.edition_id)
        .where(Edition.subscriber_id == subscriber_id)
        .where(Edition.status.in_(("preview", "queued", "sending", "sent")))
    ).all()
    history: dict[str, datetime] = {}
    for content_id, created_at in rows:
        prev = history.get(content_id)
        if prev is None or created_at > prev:
            history[content_id] = created_at
    return history


def feedback_map(db: Session, subscriber_id: str) -> dict[str, str]:
    rows = db.execute(
        select(Feedback.content_id, Feedback.signal).where(Feedback.subscriber_id == subscriber_id)
    ).all()
    return {content_id: signal for content_id, signal in rows}


def _candidates(db: Session, profile: Profile) -> list[ContentItem]:
    topics = profile.topics or list(TOPIC_LABELS)
    return list(db.scalars(select(ContentItem).where(ContentItem.topic.in_(topics))).all())


def _variety_penalty(item: ContentItem, chosen: list[ContentItem]) -> float:
    penalty = 0.0
    for other in chosen:
        if other.creator == item.creator:
            penalty += SAME_CREATOR_PENALTY
        if other.fmt == item.fmt:
            penalty += SAME_FORMAT_PENALTY
        if other.topic == item.topic:
            penalty += SAME_TOPIC_PENALTY
        overlap = set(other.interests or []) & set(item.interests or [])
        penalty += SAME_INTEREST_PENALTY * len(overlap)
    return penalty


def _pick(
    pool: list[tuple[ContentItem, float]],
    chosen: list[ContentItem],
) -> tuple[ContentItem, float] | None:
    best: tuple[ContentItem, float] | None = None
    best_key: tuple[float, str] | None = None
    for item, score in pool:
        adjusted = score - _variety_penalty(item, chosen)
        key = (-adjusted, item.id)  # stable tie-break by id
        if best_key is None or key < best_key:
            best, best_key = (item, score), key
    return best


def select_edition(
    db: Session,
    profile: Profile,
    subscriber_id: str | None = None,
    size: int = EDITION_SIZE,
) -> list[Selection]:
    """Pick `size` distinct pieces for this profile, freshest relevant material first."""
    history = delivered_history(db, subscriber_id) if subscriber_id else {}
    feedback = feedback_map(db, subscriber_id) if subscriber_id else {}

    scored = [(item, base_score(item, profile, feedback)) for item in _candidates(db, profile)]
    # Never surface material the reader explicitly asked to see less of unless nothing else exists.
    scored.sort(key=lambda pair: (-pair[1], pair[0].id))

    unseen = [(i, s) for i, s in scored if i.id not in history]
    seen = sorted(
        ((i, s) for i, s in scored if i.id in history),
        key=lambda pair: (history[pair[0].id], -pair[1], pair[0].id),
    )

    selections: list[Selection] = []
    chosen: list[ContentItem] = []

    def take(pool: list[tuple[ContentItem, float]], revisit: bool) -> None:
        while len(selections) < size and pool:
            # Revisits go strictly oldest-first; the pool is already in that order.
            picked = pool[0] if revisit else _pick(pool, chosen)
            if picked is None:
                return
            item, score = picked
            pool.remove(picked)
            chosen.append(item)
            selections.append(
                Selection(
                    content=item,
                    reason=explain(item, profile, is_revisit=revisit),
                    is_revisit=revisit,
                    score=score,
                )
            )

    take(unseen, revisit=False)
    if len(selections) < size:
        # Suitable unseen content is exhausted: resurface the least-recently-delivered
        # relevant pieces, labelled honestly as revisits.
        take(seen, revisit=True)
    return selections


def compose_intro(profile: Profile, selections: list[Selection]) -> str:
    topic_names = [TOPIC_LABELS.get(t, t) for t in profile.topics]
    interest_names = [INTEREST_LABELS.get(i, i) for i in profile.interests]
    focus = interest_names or topic_names

    def join(names: list[str]) -> str:
        names = [n.lower() for n in names]
        if len(names) == 1:
            return names[0]
        return ", ".join(names[:-1]) + " and " + names[-1]

    formats = {s.content.fmt for s in selections}
    if formats == {"video"}:
        shape = "three things to watch"
    elif "video" in formats:
        shape = "a mix of reading and watching"
    else:
        shape = "three things to read"

    minutes = sum(s.content.duration_minutes for s in selections)
    return (
        f"Today's edition follows {join(focus)}. "
        f"Three pieces, {shape}, about {minutes} minutes if you take all of them end to end. "
        "The summaries below stand on their own, so skim them and follow only what pulls at you."
    )
