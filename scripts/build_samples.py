"""Rebuild the three sample editions shown on the landing page and at /sample.

Each sample is a real edition: the selection engine runs against the real library
for an example reader's interests, so what a visitor reads before signing up is
produced by the same code that will build their own edition.

The first sample is the hand-written one that shipped with the design and is kept
verbatim; only its profile label is added.

Run: .venv/bin/python scripts/build_samples.py
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# One example reader per sample, written to look unmistakably unlike the others:
# a designer, a philosophy reader, an AI/tech reader.
SAMPLES: list[dict] = [
    {
        "slug": "philosophy-psychology",
        "label": "Philosophy + psychology",
        "reader": "for a reader who picked philosophy and psychology",
        "keep": True,
    },
    {
        "slug": "product-design",
        "label": "Product design",
        "reader": "for a reader who picked product design, typography and craft",
        "profile": {
            "topics": ["product-design"],
            "interests": ["typography", "craft-and-process"],
            "depth": "mix",
        },
        "theme": "The craft hiding<br><em>in the details.</em>",
        "subject": "Three good finds, just for you.",
        "preheader": "Type, craft, and the decisions nobody notices until they are wrong.",
    },
    {
        "slug": "ai-tech",
        "label": "AI + tech",
        "reader": "for a reader who picked AI and tech",
        "profile": {
            "topics": ["ai", "tech"],
            "interests": ["how-models-work", "software-craft"],
            "depth": "deep",
        },
        "theme": "What the machines do<br><em>when you look closely.</em>",
        "subject": "Three good finds, just for you.",
        "preheader": "Models, software craft, and the arguments underneath both.",
    },
]


def isolated_database() -> None:
    tmp = tempfile.mkdtemp(prefix="feather-samples-")
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/samples.db"
    os.environ["EMAIL_PROVIDER"] = "console"
    os.environ["SCHEDULER_ENABLED"] = "false"


def short_form(text: str) -> str:
    """A two-line snippet for the hero's miniature front page."""
    words = text.rstrip(".").split()
    if len(words) > 7:
        words = words[:7]
    half, best = sum(len(w) for w in words) / 2, 1
    run = 0.0
    for index, word in enumerate(words[:-1], start=1):
        run += len(word)
        if abs(run - half) < abs(sum(len(w) for w in words[:best]) - half):
            best = index
    return " ".join(words[:best]) + "<br>" + " ".join(words[best:])


def build(sample: dict) -> dict:
    from app.content import INTEREST_LABELS, TOPIC_LABELS
    from app.db import SessionLocal
    from app.selection import Profile, compose_intro, select_edition

    profile = Profile.from_dict(sample["profile"])
    db = SessionLocal()
    try:
        selections = select_edition(db, profile)
        stories = []
        for index, selection in enumerate(selections, start=1):
            piece = selection.content
            interest = (piece.interests or [None])[0]
            topic = TOPIC_LABELS.get(piece.topic, piece.topic)
            if interest:
                topic = f"{topic} × {INTEREST_LABELS.get(interest, interest).lower()}"
            stories.append(
                {
                    "id": f"0{index}",
                    "topic": topic,
                    "title": piece.display_title,
                    "short": short_form(piece.display_title),
                    "original": piece.title,
                    "by": piece.creator,
                    "publication": piece.publication,
                    "url": piece.url,
                    "duration": f"Original · {piece.duration_minutes} min {piece.verb}",
                    "image": piece.thumbnail_url,
                    "alt": piece.alt_text,
                    "credit": piece.credit,
                    "summary": piece.summary,
                    "note": selection.reason or piece.note or "",
                }
            )
    finally:
        db.close()
    interests = " + ".join(INTEREST_LABELS.get(i, i) for i in profile.interests) or " + ".join(
        TOPIC_LABELS.get(t, t) for t in profile.topics
    )
    return {
        "slug": sample["slug"],
        "label": sample["label"],
        "reader": sample["reader"],
        "theme": sample["theme"],
        "interests": f"SAMPLE EDITION · {interests.upper()}",
        "subject": sample["subject"],
        "preheader": sample["preheader"],
        "intro": compose_intro(profile, selections),
        "stories": stories,
    }


def main() -> None:
    isolated_database()
    from app.content import seed_content
    from app.db import SessionLocal, init_db

    init_db()
    db = SessionLocal()
    try:
        seed_content(db)
    finally:
        db.close()

    editions = []
    for sample in SAMPLES:
        if sample.get("keep"):
            original = json.loads((ROOT / "content" / "sample-edition.json").read_text())
            editions.append(
                {
                    "slug": sample["slug"],
                    "label": sample["label"],
                    "reader": sample["reader"],
                    **original,
                }
            )
            continue
        editions.append(build(sample))

    out = ROOT / "content" / "sample-editions.json"
    out.write_text(json.dumps(editions, indent=2, ensure_ascii=False) + "\n")
    for edition in editions:
        titles = ", ".join(story["original"] for story in edition["stories"])
        print(f"{edition['slug']}: {titles}")
    print(f"wrote {out.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
