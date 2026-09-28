"""Prove that editions are personalised, not one newsletter mailed to everybody.

Builds a real edition for each profile in ``PROFILE_MATRIX`` against the real
content library, writes the newsletter each profile would receive to disk so it
can be opened and read, and reports how much any two profiles share.

Everything runs in a throwaway database, so this never touches subscriber data.

Run: .venv/bin/python scripts/personalization_report.py [--out DIR]
"""

from __future__ import annotations

import argparse
import itertools
import os
import tempfile
from dataclasses import dataclass
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Profiles chosen to pull on every lever the engine has: each launch topic alone,
# subtopics inside one topic, two topics together, and the same topics at
# opposite reading depths.
PROFILE_MATRIX: list[tuple[str, dict]] = [
    ("design-typography", {"topics": ["product-design"], "interests": ["typography"], "depth": "mix"}),
    ("design-systems", {"topics": ["product-design"], "interests": ["design-systems"], "depth": "deep"}),
    ("philosophy", {"topics": ["philosophy"], "interests": [], "depth": "mix"}),
    ("philosophy-ethics", {"topics": ["philosophy"], "interests": ["ethics"], "depth": "deep"}),
    (
        "psychology-attention",
        {"topics": ["psychology"], "interests": ["attention-and-focus"], "depth": "mix"},
    ),
    (
        "psychology-memory",
        {"topics": ["psychology"], "interests": ["learning-and-memory"], "depth": "accessible"},
    ),
    ("ai-safety", {"topics": ["ai"], "interests": ["ai-safety"], "depth": "deep"}),
    ("tech-accessible", {"topics": ["tech"], "interests": [], "depth": "accessible"}),
    ("tech-deep", {"topics": ["tech"], "interests": [], "depth": "deep"}),
    ("ai-and-tech", {"topics": ["ai", "tech"], "interests": ["software-craft"], "depth": "mix"}),
    ("politics-geopolitics", {"topics": ["politics"], "interests": ["geopolitics"], "depth": "mix"}),
    ("politics-policy", {"topics": ["politics"], "interests": ["public-policy"], "depth": "deep"}),
    ("business-markets", {"topics": ["business"], "interests": ["stock-market"], "depth": "mix"}),
    (
        "business-strategy",
        {"topics": ["business"], "interests": ["business-strategy"], "depth": "accessible"},
    ),
    (
        "business-and-politics",
        {"topics": ["business", "politics"], "interests": ["economics"], "depth": "deep"},
    ),
]


@dataclass
class Built:
    name: str
    profile: dict
    picks: list[dict]
    html: str
    text: str

    @property
    def ids(self) -> set[str]:
        return {pick["id"] for pick in self.picks}


def isolated_database() -> None:
    """Point the app at a scratch database before anything imports it."""
    tmp = tempfile.mkdtemp(prefix="feather-personalisation-")
    os.environ["DATABASE_URL"] = f"sqlite:///{tmp}/report.db"
    os.environ["OUTBOX_DIR"] = f"{tmp}/outbox"
    os.environ["EMAIL_PROVIDER"] = "console"
    os.environ["SCHEDULER_ENABLED"] = "false"
    os.environ.setdefault("BASE_URL", "https://feather-press-b77h.onrender.com")


def build_matrix() -> list[Built]:
    """One edition per profile, rendered exactly as the subscriber would receive it."""
    from app.content import seed_content
    from app.db import SessionLocal, init_db
    from app.models import Subscriber
    from app.render import render_edition_email
    from app.selection import Profile
    from app.services import build_edition

    init_db()
    db = SessionLocal()
    seed_content(db)
    built: list[Built] = []
    try:
        for name, raw in PROFILE_MATRIX:
            profile = Profile.from_dict(raw)
            subscriber = Subscriber(
                email=f"{name}@example.invalid",
                status="active",
                topics=profile.topics,
                interests=profile.interests,
                depth=profile.depth,
            )
            db.add(subscriber)
            db.commit()
            edition = build_edition(db, profile, subscriber=subscriber, kind="daily")
            _, html, text = render_edition_email(edition, date.today())
            picks = [
                {
                    "id": item.content.id,
                    "title": item.content.title,
                    "publication": item.content.publication,
                    "topic": item.content.topic,
                    "interests": list(item.content.interests),
                    "depth": item.content.depth,
                    "reason": item.reason,
                }
                for item in edition.items
            ]
            built.append(Built(name=name, profile=raw, picks=picks, html=html, text=text))
    finally:
        db.close()
    return built


def overlaps(built: list[Built]) -> list[tuple[str, str, int]]:
    return [(a.name, b.name, len(a.ids & b.ids)) for a, b in itertools.combinations(built, 2)]


def relevance_failures(built: list[Built]) -> list[str]:
    """Every piece must sit in a chosen topic, and chosen subtopics must show up."""
    from app.content import INTEREST_LABELS, TOPIC_LABELS

    problems: list[str] = []
    for edition in built:
        unknown = [slug for slug in edition.profile["topics"] if slug not in TOPIC_LABELS]
        unknown += [slug for slug in edition.profile["interests"] if slug not in INTEREST_LABELS]
        problems += [f"{edition.name}: '{slug}' is not a slug the library offers" for slug in unknown]
        topics = set(edition.profile["topics"])
        interests = set(edition.profile["interests"])
        for pick in edition.picks:
            if pick["topic"] not in topics:
                problems.append(
                    f"{edition.name}: '{pick['title']}' is {pick['topic']}, not in {sorted(topics)}"
                )
        if interests and not any(interests & set(pick["interests"]) for pick in edition.picks):
            problems.append(f"{edition.name}: nothing matched the chosen subtopics {sorted(interests)}")
        if len(edition.ids) != 3:
            problems.append(f"{edition.name}: {len(edition.ids)} distinct pieces, expected 3")
    return problems


def write_report(built: list[Built], out: Path) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    for edition in built:
        (out / f"{edition.name}.html").write_text(edition.html)
        (out / f"{edition.name}.txt").write_text(edition.text)

    pairs = overlaps(built)
    used = {pick["id"] for edition in built for pick in edition.picks}
    lines = [
        "# Personalisation report",
        "",
        f"{len(built)} profiles, {len(used)} distinct pieces across {len(built) * 3} slots.",
        "",
        "## What each profile receives",
        "",
    ]
    for edition in built:
        lines += [
            f"### {edition.name}",
            "",
            f"topics: {', '.join(edition.profile['topics'])} · "
            f"subtopics: {', '.join(edition.profile['interests']) or 'none'} · "
            f"depth: {edition.profile['depth']}",
            "",
            "| # | Title | Publication | Topic | Depth | Serius's note |",
            "| - | ----- | ----------- | ----- | ----- | ------------- |",
        ]
        for position, pick in enumerate(edition.picks, start=1):
            lines.append(
                f"| {position} | {pick['title']} | {pick['publication']} | "
                f"{pick['topic']} | {pick['depth']} | {pick['reason'] or '—'} |"
            )
        lines += ["", f"Newsletter as sent: `{edition.name}.html` / `{edition.name}.txt`", ""]

    lines += ["## Shared pieces between profiles", "", "| A | B | shared of 3 |", "| - | - | ----------- |"]
    for a, b, shared in sorted(pairs, key=lambda row: -row[2]):
        lines.append(f"| {a} | {b} | {shared} |")

    problems = relevance_failures(built)
    identical = [f"{a} and {b}" for a, b, shared in pairs if shared == 3]
    lines += [
        "",
        "## Verdict",
        "",
        f"- identical editions: {', '.join(identical) if identical else 'none'}",
        f"- off-topic or unmatched selections: {len(problems)}",
    ]
    lines += [f"  - {problem}" for problem in problems]

    report = out / "report.md"
    report.write_text("\n".join(lines) + "\n")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "personalization-report")
    args = parser.parse_args()

    isolated_database()
    built = build_matrix()
    report = write_report(built, args.out)

    pairs = overlaps(built)
    worst = max(pairs, key=lambda row: row[2])
    problems = relevance_failures(built)
    print(f"{len(built)} profiles → {len({pick['id'] for e in built for pick in e.picks})} distinct pieces")
    print(f"most alike: {worst[0]} vs {worst[1]} share {worst[2]} of 3")
    for problem in problems:
        print(f"PROBLEM {problem}")
    print(f"report: {report}")
    return 1 if problems or worst[2] == 3 else 0


if __name__ == "__main__":
    raise SystemExit(main())
