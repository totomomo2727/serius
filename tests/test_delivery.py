from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.models import Edition, Subscriber
from app.selection import Profile
from app.services import (
    build_edition,
    due_today,
    run_daily,
    send_edition,
    subscribe,
    verify,
)
from app.tokens import MANAGE, VERIFY, InvalidToken, make_token, resolve


def make_subscriber(db, email="reader@example.com", tz="UTC", status="active") -> Subscriber:
    subscriber = Subscriber(email=email, topics=["ai"], interests=[], depth="mix", timezone=tz, status=status)
    db.add(subscriber)
    db.commit()
    return subscriber


def test_first_edition_is_exactly_the_previewed_one(db):
    profile = Profile(topics=["philosophy"], interests=["stoicism"], depth="mix")
    preview = build_edition(db, profile)
    previewed_ids = [i.content_id for i in preview.items]

    subscriber, first = subscribe(db, "preview@example.com", profile, "UTC", preview)
    assert first.id == preview.id
    verify(db, subscriber)
    result = send_edition(db, first)

    assert result.ok
    assert [i.content_id for i in first.items] == previewed_ids
    assert first.status == "sent"


def test_sending_twice_is_idempotent(db):
    subscriber = make_subscriber(db)
    edition = build_edition(db, Profile.from_dict(subscriber.profile), subscriber=subscriber, kind="daily")
    send_edition(db, edition)
    message_id = edition.provider_message_id
    attempts = edition.attempts
    send_edition(db, edition)
    assert edition.attempts == attempts
    assert edition.provider_message_id == message_id


def test_daily_job_is_idempotent_across_runs(db):
    make_subscriber(db)
    noon = datetime(2026, 5, 4, 12, 0, tzinfo=UTC)
    first = run_daily(db, now=noon)
    second = run_daily(db, now=noon)
    assert first["sent"] == 1
    assert second["sent"] == 0
    assert second["already_sent"] == 1
    assert db.query(Edition).filter(Edition.status == "sent").count() == 1


def test_delivery_waits_for_eight_am_local_time(db):
    tokyo = make_subscriber(db, "tokyo@example.com", tz="Asia/Tokyo")
    new_york = make_subscriber(db, "ny@example.com", tz="America/New_York")
    # 2026-05-04 10:00 UTC is 19:00 in Tokyo and 06:00 in New York.
    moment = datetime(2026, 5, 4, 10, 0, tzinfo=UTC)
    assert due_today(tokyo, moment) is True
    assert due_today(new_york, moment) is False
    report = run_daily(db, now=moment)
    sent = [d["email"] for d in report["details"] if d["outcome"] == "sent"]
    assert sent == ["tokyo@example.com"]


def test_paused_and_unsubscribed_subscribers_get_nothing(db):
    make_subscriber(db, "paused@example.com", status="paused")
    make_subscriber(db, "gone@example.com", status="unsubscribed")
    report = run_daily(db, now=datetime(2026, 5, 4, 12, 0, tzinfo=UTC), force=True)
    assert report["considered"] == 0
    assert db.query(Edition).count() == 0


def test_failed_send_can_be_retried_without_duplicating(db, monkeypatch):
    make_subscriber(db, "flaky@example.com")
    import app.services as services
    from app.emailer import SendResult

    monkeypatch.setattr(
        services, "send_email", lambda *a, **k: SendResult(ok=False, provider="resend", error="boom")
    )
    report = run_daily(db, now=datetime(2026, 5, 4, 12, 0, tzinfo=UTC))
    assert report["failed"] == 1
    edition = db.query(Edition).one()
    assert edition.status == "failed" and edition.error == "boom"

    monkeypatch.setattr(
        services,
        "send_email",
        lambda *a, **k: SendResult(ok=True, provider="resend", message_id="m1", delivered=True),
    )
    run_daily(db, now=datetime(2026, 5, 4, 12, 30, tzinfo=UTC))
    assert db.query(Edition).count() == 1
    edition = db.query(Edition).one()
    assert edition.status == "sent" and edition.attempts == 2


def test_tokens_are_purpose_scoped_and_versioned(db):
    subscriber = make_subscriber(db, "tokens@example.com")
    manage = make_token(subscriber, MANAGE)
    assert resolve(db, manage, MANAGE).id == subscriber.id
    with pytest.raises(InvalidToken):
        resolve(db, manage, VERIFY)
    with pytest.raises(InvalidToken):
        resolve(db, manage + "x", MANAGE)
    subscriber.token_version += 1
    db.commit()
    with pytest.raises(InvalidToken):
        resolve(db, manage, MANAGE)


def test_rendered_email_has_masthead_summaries_links_and_footer(db):
    from app.render import render_edition_email

    subscriber = make_subscriber(db, "render@example.com")
    edition = build_edition(db, Profile.from_dict(subscriber.profile), subscriber=subscriber, kind="daily")
    subject, html, text = render_edition_email(edition, datetime(2026, 5, 4).date())

    assert "Your edition" in subject
    for body in (html, text):
        assert "The Feather Press" in body or "THE FEATHER PRESS" in body
        assert "The rest of the day is yours." in body
        assert "unsubscribe" in body.lower()
        for item in edition.items:
            assert item.content.title in body
            assert item.content.creator in body
            assert item.content.url in body
            assert item.content.read_label.lower() in body.lower()
    # The plain-text edition must stand alone when images and CSS are blocked.
    assert "<" not in text.replace("<https", "")


def test_resubscribing_after_todays_edition_still_sends(db):
    """A second subscription the same day must not collide with the day's existing edition."""
    profile = Profile(topics=["tech"], interests=[], depth="mix")
    subscriber, first = subscribe(db, "again@example.com", profile, "UTC", build_edition(db, profile))
    verify(db, subscriber)
    assert send_edition(db, first).ok

    later = build_edition(db, profile)
    subscriber, second = subscribe(db, "again@example.com", profile, "UTC", later)
    verify(db, subscriber)
    result = send_edition(db, second)

    assert result.ok
    assert second.id != first.id
    assert second.status == "sent"
