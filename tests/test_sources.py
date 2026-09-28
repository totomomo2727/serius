"""Source quality and media mix: what gets recommended, and why."""

from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from app import content
from app.content import load_library
from app.models import ContentItem, Subscriber
from app.selection import Profile, base_score, select_edition
from app.services import build_edition
from app.sources import DEFAULT_TIER, is_platform, source_score, tier_for


def test_tier_is_inferred_from_the_publisher():
    assert tier_for("https://paulgraham.com/greatwork.html") == 1
    assert tier_for("https://aeon.co/essays/anything") == 1
    assert tier_for("https://www.nngroup.com/articles/ten-usability-heuristics/") == 2
    assert tier_for("https://example.com/listicle") == DEFAULT_TIER


def test_a_declared_tier_wins_over_inference():
    # Hosting says nothing about quality, so platform-hosted pieces state their own.
    assert is_platform("https://www.youtube.com/watch?v=aircAruvnKk")
    assert tier_for("https://www.youtube.com/watch?v=aircAruvnKk", 1) == 1
    assert tier_for("https://paulgraham.com/greatwork.html", 3) == 3


def test_platform_hosted_pieces_must_declare_a_tier(tmp_path, monkeypatch):
    record = {
        "id": "x-platform",
        "title": "T",
        "creator": "C",
        "publication": "P",
        "url": "https://www.youtube.com/watch?v=abc",
        "format": "video",
        "topic": "ai",
        "interests": ["ai-safety"],
        "depth": "deep",
        "duration_minutes": 10,
        "summary": "s",
        "verified_on": "2026-09-26",
    }
    (tmp_path / "ai.json").write_text(json.dumps([record]))
    monkeypatch.setattr(content, "LIBRARY_DIR", tmp_path)
    with pytest.raises(ValueError, match="source_tier"):
        load_library()
    (tmp_path / "ai.json").write_text(json.dumps([{**record, "source_tier": 1}]))
    assert load_library()[0]["source_tier"] == 1


def test_the_library_carries_podcasts_across_topics(db):
    podcasts = db.scalars(select(ContentItem).where(ContentItem.fmt == "podcast")).all()
    assert len(podcasts) >= 8
    assert len({p.topic for p in podcasts}) >= 4
    for podcast in podcasts:
        assert podcast.mode == "listen"
        assert podcast.read_label == "Listen to the episode"
        assert podcast.source_tier in (1, 2, 3)


def _piece(item_id: str, interests: list[str], tier: int) -> ContentItem:
    return ContentItem(
        id=item_id,
        title=item_id,
        creator="C",
        publication="P",
        url=f"https://example.com/{item_id}",
        fmt="essay",
        topic="ai",
        interests=interests,
        depth="deep",
        duration_minutes=10,
        summary="s",
        verified_on="2026-09-26",
        source_tier=tier,
    )


def test_a_better_source_wins_between_otherwise_equal_pieces():
    profile = Profile(topics=["ai"], interests=["how-models-work"], depth="mix")
    primary = _piece("a", ["how-models-work"], 1)
    specialist = _piece("b", ["how-models-work"], 2)
    general = _piece("c", ["how-models-work"], 3)
    assert base_score(primary, profile) > base_score(specialist, profile)
    assert base_score(specialist, profile) > base_score(general, profile)


def test_relevance_still_outranks_prestige():
    """A tier-3 piece on your actual interest beats a tier-1 piece that is merely on-topic."""
    profile = Profile(topics=["ai"], interests=["how-models-work"], depth="mix")
    on_interest = _piece("weak-source", ["how-models-work"], 3)
    prestigious = _piece("strong-source", ["ai-safety"], 1)
    assert base_score(on_interest, profile) > base_score(prestigious, profile)


def test_the_library_spans_source_tiers(db):
    tiers = {item.source_tier for item in db.scalars(select(ContentItem)).all()}
    assert tiers >= {1, 2}


def test_source_score_is_bounded_below_one_interest_match():
    assert source_score(1) < 10.0
    assert source_score(3) == 0.0


@pytest.mark.parametrize(
    "topics,interests",
    [
        (["psychology"], ["attention-and-focus"]),
        (["ai"], ["ai-safety"]),
        (["product-design"], ["typography"]),
        (["philosophy"], ["meaning-and-mortality"]),
    ],
)
def test_editions_mix_what_the_reader_does_with_them(db, topics, interests):
    picks = select_edition(db, Profile(topics=topics, interests=interests, depth="mix"))
    assert len({p.content.mode for p in picks}) >= 2


def test_variety_never_drags_in_an_off_topic_piece(db):
    for topics in (["ai"], ["tech"], ["philosophy"], ["psychology"], ["product-design"]):
        picks = select_edition(db, Profile(topics=topics, interests=[], depth="mix"))
        assert {p.content.topic for p in picks} == set(topics)


def test_mixing_modes_does_not_resurface_delivered_pieces(db):
    profile = Profile(topics=["psychology"], interests=["attention-and-focus"], depth="mix")
    subscriber = Subscriber(
        email="mix@example.com",
        status="active",
        topics=profile.topics,
        interests=profile.interests,
        depth=profile.depth,
        timezone="UTC",
    )
    db.add(subscriber)
    db.commit()
    first = build_edition(db, profile, subscriber, kind="daily", status="sent")
    second = build_edition(db, profile, subscriber, kind="daily", status="sent")
    delivered = {i.content_id for i in first.items}
    for item in second.items:
        assert item.content_id not in delivered
