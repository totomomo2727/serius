"""Every profile gets its own newspaper — no single edition mailed to everyone."""

from __future__ import annotations

import itertools

from app.content import INTEREST_LABELS, TOPIC_LABELS
from app.selection import Profile, select_edition
from scripts.personalization_report import PROFILE_MATRIX


def picks_for(db, raw: dict) -> list:
    return select_edition(db, Profile.from_dict(raw))


def test_the_matrix_only_uses_slugs_the_library_offers():
    for _, raw in PROFILE_MATRIX:
        assert all(slug in TOPIC_LABELS for slug in raw["topics"])
        assert all(slug in INTEREST_LABELS for slug in raw["interests"])


def test_every_profile_gets_three_on_topic_pieces(db):
    for name, raw in PROFILE_MATRIX:
        picks = picks_for(db, raw)
        assert len({p.content.id for p in picks}) == 3, name
        assert all(p.content.topic in set(raw["topics"]) for p in picks), name


def test_chosen_subtopics_actually_appear(db):
    for name, raw in PROFILE_MATRIX:
        if not raw["interests"]:
            continue
        wanted = set(raw["interests"])
        assert any(wanted & set(p.content.interests) for p in picks_for(db, raw)), name


def test_no_two_profiles_receive_the_same_edition(db):
    editions = {name: {p.content.id for p in picks_for(db, raw)} for name, raw in PROFILE_MATRIX}
    for a, b in itertools.combinations(editions, 2):
        shared = editions[a] & editions[b]
        assert len(shared) < 3, f"{a} and {b} would receive the same newspaper"


def test_the_matrix_spreads_across_the_library(db):
    """A generic newsletter would reuse the same handful of pieces everywhere."""
    used = [p.content.id for _, raw in PROFILE_MATRIX for p in picks_for(db, raw)]
    assert len(set(used)) >= int(len(used) * 0.8)
