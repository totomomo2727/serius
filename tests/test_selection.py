from __future__ import annotations

from app.models import Edition, EditionItem, Subscriber
from app.selection import Profile, select_edition
from app.services import build_edition


def titles(selections) -> list[str]:
    return [s.content.title for s in selections]


def test_three_distinct_items(db):
    picks = select_edition(db, Profile(topics=["ai"], interests=["ai-safety"], depth="mix"))
    assert len(picks) == 3
    assert len({p.content.id for p in picks}) == 3


def test_items_are_relevant_to_the_chosen_topic(db):
    picks = select_edition(db, Profile(topics=["philosophy"], interests=[], depth="mix"))
    assert {p.content.topic for p in picks} == {"philosophy"}


def test_interests_outrank_broad_topic(db):
    picks = select_edition(db, Profile(topics=["psychology"], interests=["learning-and-memory"], depth="mix"))
    matched = [p for p in picks if "learning-and-memory" in p.content.interests]
    assert len(matched) >= 2


def test_depth_preference_is_respected(db):
    accessible = select_edition(db, Profile(topics=["tech"], interests=[], depth="accessible"))
    deep = select_edition(db, Profile(topics=["tech"], interests=[], depth="deep"))
    assert sum(1 for p in accessible if p.content.depth == "accessible") >= 2
    assert sum(1 for p in deep if p.content.depth == "deep") >= 2


def test_different_profiles_get_different_editions(db):
    a = select_edition(db, Profile(topics=["product-design"], interests=["typography"], depth="mix"))
    b = select_edition(db, Profile(topics=["ai"], interests=["ai-safety"], depth="mix"))
    assert not ({p.content.id for p in a} & {p.content.id for p in b})


def test_selection_is_deterministic_for_the_same_profile(db):
    profile = Profile(topics=["tech", "ai"], interests=["software-craft"], depth="deep")
    assert titles(select_edition(db, profile)) == titles(select_edition(db, profile))


def test_every_item_has_complete_attribution_and_a_real_reason(db):
    profile = Profile(topics=["psychology"], interests=["attention-and-focus"], depth="mix")
    picks = select_edition(db, profile)
    for pick in picks:
        item = pick.content
        assert item.title and item.creator and item.publication
        assert item.url.startswith("http")
        assert 60 <= len(item.summary.split()) <= 90
        assert item.read_label in (
            "Read the article",
            "Read the essay",
            "Watch the video",
            "Listen to the episode",
        )
        # A note is optional, but when it appears it stays to one short line.
        assert len(pick.reason.split()) <= 12
    assert any(pick.reason for pick in picks)
    notes = [pick.reason for pick in picks if pick.reason]
    assert len(notes) == len(set(notes))  # never the same sentence three times


def test_previously_delivered_items_are_avoided(db):
    subscriber = Subscriber(email="repeat@example.com", topics=["ai"], interests=[], depth="mix")
    db.add(subscriber)
    db.commit()
    profile = Profile.from_dict(subscriber.profile)
    first = build_edition(db, profile, subscriber=subscriber, kind="daily")
    second = select_edition(db, profile, subscriber_id=subscriber.id)
    seen = {i.content_id for i in first.items}
    assert not (seen & {p.content.id for p in second})
    assert all(not p.is_revisit for p in second)


def test_revisits_are_labelled_once_the_library_is_exhausted(db):
    subscriber = Subscriber(email="exhausted@example.com", topics=["ai"], interests=[], depth="mix")
    db.add(subscriber)
    db.commit()
    profile = Profile.from_dict(subscriber.profile)
    for _ in range(6):  # ai has 16 items: six editions exhausts it
        build_edition(db, profile, subscriber=subscriber, kind="daily")
    picks = select_edition(db, profile, subscriber_id=subscriber.id)
    assert any(p.is_revisit for p in picks)
    assert all("revisit" in p.reason.lower() for p in picks if p.is_revisit)
    assert len({p.content.id for p in picks}) == 3


def test_preference_changes_change_future_editions(db):
    subscriber = Subscriber(
        email="switcher@example.com", topics=["product-design"], interests=[], depth="mix"
    )
    db.add(subscriber)
    db.commit()
    before = select_edition(db, Profile.from_dict(subscriber.profile), subscriber_id=subscriber.id)
    subscriber.topics = ["philosophy"]
    db.commit()
    after = select_edition(db, Profile.from_dict(subscriber.profile), subscriber_id=subscriber.id)
    assert {p.content.topic for p in before} != {p.content.topic for p in after}


def test_edition_persists_profile_and_items(db):
    profile = Profile(topics=["tech"], interests=["internet-history"], depth="accessible")
    edition = build_edition(db, profile)
    stored = db.get(Edition, edition.id)
    assert stored.profile_snapshot == profile.to_dict()
    assert db.query(EditionItem).filter_by(edition_id=edition.id).count() == 3
    assert stored.intro
