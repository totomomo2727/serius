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


def in_sentence(label: str) -> str:
    """Lowercase a label for mid-sentence use, leaving acronyms such as AI alone."""
    return label if label.isupper() else label.lower()


def explain(item: ContentItem, profile: Profile, is_revisit: bool = False) -> str:
    """One short note, or nothing when the topic label already says it all."""
    if is_revisit:
        return "A revisit: the closest match left in the library today."
    matched = [i for i in (item.interests or []) if i in profile.interests]
    if matched:
        label = in_sentence(INTEREST_LABELS.get(matched[0], matched[0]))
        return f"For your interest in {label}."
    if item.topic in profile.topics:
        if profile.depth != "mix" and item.depth == profile.depth:
            kind = "An accessible" if item.depth == "accessible" else "A deeper"
            topic = in_sentence(TOPIC_LABELS.get(item.topic, item.topic))
            return f"{kind} piece from your {topic} choice."
        return ""
    return f"Near your choices, from {in_sentence(TOPIC_LABELS.get(item.topic, item.topic))}."


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

    # The same note three times reads like a template, so keep only its first appearance.
    seen_reasons: set[str] = set()
    for selection in selections:
        if selection.is_revisit:
            continue  # a revisit always says so
        if selection.reason and selection.reason in seen_reasons:
            selection.reason = ""
        seen_reasons.add(selection.reason)
    return selections


def compose_intro(profile: Profile, selections: list[Selection]) -> str:
    """One line naming what is actually in today's three finds."""
    names: list[str] = []
    for selection in selections:
        item = selection.content
        matched = [i for i in (item.interests or []) if i in profile.interests]
        label = in_sentence(
            INTEREST_LABELS.get(matched[0], matched[0])
            if matched
            else TOPIC_LABELS.get(item.topic, item.topic)
        )
        if label not in names:
            names.append(label)
    if not names:
        names = [in_sentence(TOPIC_LABELS.get(t, t)) for t in profile.topics][:3]
    # Several labels already contain "and" ("attention and focus"), so a second
    # conjunction reads badly: comma-separate those instead.
    if len(names) == 1:
        joined = names[0]
    elif any(" and " in name for name in names):
        joined = ", ".join(names)
    elif len(names) == 2:
        joined = f"{names[0]} and {names[1]}"
    else:
        joined = ", ".join(names[:-1]) + ", and " + names[-1]
    return f"Today: {joined}."
