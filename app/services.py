"""Subscription and delivery workflow.

Sending is idempotent: an edition row is the unit of work, it is unique per
(subscriber, local date), and a row already marked ``sent`` is never sent twice no
matter how often the scheduler or a retry runs.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.emailer import SendResult, send_email
from app.models import Edition, EditionItem, RateLimit, Subscriber, utcnow
from app.render import render_edition_email, render_verification_email
from app.selection import Profile, compose_intro, select_edition
from app.tokens import manage_url, unsubscribe_url

ACTIVE = "active"
PENDING = "pending"
PAUSED = "paused"
UNSUBSCRIBED = "unsubscribed"


def safe_zone(name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(name or "UTC")
    except (ZoneInfoNotFoundError, ValueError):
        return ZoneInfo("UTC")


def local_now(subscriber: Subscriber, now: datetime | None = None) -> datetime:
    now = now or datetime.now(tz=UTC)
    return now.astimezone(safe_zone(subscriber.timezone))


# --------------------------------------------------------------------------- editions


def build_edition(
    db: Session,
    profile: Profile,
    subscriber: Subscriber | None = None,
    kind: str = "preview",
    edition_date: date | None = None,
    status: str = "preview",
) -> Edition:
    selections = select_edition(db, profile, subscriber_id=subscriber.id if subscriber else None)
    edition = Edition(
        subscriber_id=subscriber.id if subscriber else None,
        edition_date=edition_date,
        kind=kind,
        status=status,
        profile_snapshot=profile.to_dict(),
        intro=compose_intro(profile, selections),
    )
    db.add(edition)
    db.flush()
    for position, selection in enumerate(selections, start=1):
        db.add(
            EditionItem(
                edition_id=edition.id,
                content_id=selection.content.id,
                position=position,
                reason=selection.reason,
                is_revisit=selection.is_revisit,
            )
        )
    db.commit()
    db.refresh(edition)
    return edition


def latest_edition(db: Session, subscriber: Subscriber) -> Edition | None:
    return db.scalars(
        select(Edition)
        .where(Edition.subscriber_id == subscriber.id)
        .order_by(Edition.created_at.desc())
        .limit(1)
    ).first()


# ----------------------------------------------------------------------- subscription


def subscribe(
    db: Session,
    email: str,
    profile: Profile,
    tz_name: str,
    preview: Edition | None,
) -> tuple[Subscriber, Edition]:
    """Create or update a subscriber and pin the previewed edition as their first one."""
    email = email.strip().lower()
    subscriber = db.scalars(select(Subscriber).where(Subscriber.email == email)).first()
    if subscriber is None:
        subscriber = Subscriber(email=email)
        db.add(subscriber)
    subscriber.topics = list(profile.topics)
    subscriber.interests = list(profile.interests)
    subscriber.depth = profile.depth
    subscriber.timezone = tz_name
    if subscriber.status in (UNSUBSCRIBED, PENDING) or subscriber.verified_at is None:
        subscriber.status = PENDING
        # invalidate links issued by any earlier attempt
        subscriber.token_version = (subscriber.token_version or 1) + 1
    db.commit()
    db.refresh(subscriber)

    if preview is not None and preview.subscriber_id is None:
        preview.subscriber_id = subscriber.id
        preview.kind = "first"
        db.commit()
        db.refresh(preview)
        edition = preview
    else:
        edition = build_edition(db, profile, subscriber=subscriber, kind="first", status="preview")
    return subscriber, edition


def send_verification(db: Session, subscriber: Subscriber, edition: Edition) -> SendResult:
    subject, html, text = render_verification_email(subscriber, edition.items)
    result = send_email(subscriber.email, subject, html, text)
    return result


def verify(db: Session, subscriber: Subscriber) -> None:
    if subscriber.status != ACTIVE:
        subscriber.status = ACTIVE
        subscriber.verified_at = utcnow()
        subscriber.unsubscribed_at = None
        db.commit()


def pending_first_edition(db: Session, subscriber: Subscriber) -> Edition | None:
    return db.scalars(
        select(Edition)
        .where(Edition.subscriber_id == subscriber.id, Edition.kind == "first")
        .order_by(Edition.created_at.desc())
        .limit(1)
    ).first()


# ---------------------------------------------------------------------------- sending


def send_edition(db: Session, edition: Edition, local_date: date | None = None) -> SendResult:
    """Send one edition. Safe to call repeatedly: an already-sent edition is a no-op."""
    subscriber = edition.subscriber
    if subscriber is None:
        return SendResult(ok=False, provider="none", error="edition has no subscriber")
    if edition.status == "sent":
        return SendResult(
            ok=True,
            provider=edition.provider or "none",
            message_id=edition.provider_message_id,
            delivered=edition.provider != "console",
        )
    if subscriber.status not in (ACTIVE, PENDING):
        edition.status = "skipped"
        edition.error = f"subscriber is {subscriber.status}"
        db.commit()
        return SendResult(ok=False, provider="none", error=edition.error)

    local_date = local_date or local_now(subscriber).date()
    edition.status = "sending"
    edition.attempts += 1
    if edition.edition_date is None:
        edition.edition_date = local_date
    db.commit()

    subject, html, text = render_edition_email(edition, local_date)
    result = send_email(
        subscriber.email,
        subject,
        html,
        text,
        headers={
            "List-Unsubscribe": f"<{unsubscribe_url(subscriber)}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )
    if result.ok:
        edition.status = "sent"
        edition.sent_at = utcnow()
        edition.provider = result.provider
        edition.provider_message_id = result.message_id
        edition.error = None
    else:
        edition.status = "failed"
        edition.provider = result.provider
        edition.error = result.error
    db.commit()
    return result


def due_today(subscriber: Subscriber, now: datetime) -> bool:
    return local_now(subscriber, now).hour >= settings.delivery_hour_local


def run_daily(db: Session, now: datetime | None = None, force: bool = False) -> dict:
    """Send today's edition to every active subscriber whose local 8am has passed."""
    now = now or datetime.now(tz=UTC)
    report = {"considered": 0, "sent": 0, "already_sent": 0, "skipped": 0, "failed": 0, "details": []}
    subscribers = db.scalars(select(Subscriber).where(Subscriber.status == ACTIVE)).all()
    for subscriber in subscribers:
        report["considered"] += 1
        local_date = local_now(subscriber, now).date()
        if not force and not due_today(subscriber, now):
            report["skipped"] += 1
            report["details"].append({"email": subscriber.email, "outcome": "not due yet"})
            continue
        existing = db.scalars(
            select(Edition).where(Edition.subscriber_id == subscriber.id, Edition.edition_date == local_date)
        ).first()
        if existing is not None and existing.status == "sent":
            report["already_sent"] += 1
            report["details"].append({"email": subscriber.email, "outcome": "already sent"})
            continue
        edition = existing
        if edition is None:
            edition = pending_first_edition_unsent(db, subscriber)
        if edition is None:
            edition = build_edition(
                db,
                Profile.from_dict(subscriber.profile),
                subscriber=subscriber,
                kind="daily",
                edition_date=local_date,
                status="queued",
            )
        result = send_edition(db, edition, local_date=local_date)
        if result.ok:
            report["sent"] += 1
            report["details"].append(
                {"email": subscriber.email, "outcome": "sent", "provider": result.provider}
            )
        else:
            report["failed"] += 1
            report["details"].append({"email": subscriber.email, "outcome": "failed", "error": result.error})
    return report


def pending_first_edition_unsent(db: Session, subscriber: Subscriber) -> Edition | None:
    """The previewed first edition, if it has not gone out yet."""
    return db.scalars(
        select(Edition)
        .where(
            Edition.subscriber_id == subscriber.id,
            Edition.kind == "first",
            Edition.status.in_(("preview", "queued", "failed")),
        )
        .order_by(Edition.created_at.asc())
        .limit(1)
    ).first()


# ------------------------------------------------------------------------ rate limits


def rate_limited(db: Session, bucket: str, limit: int, window_minutes: int = 60) -> bool:
    cutoff = utcnow() - timedelta(minutes=window_minutes)
    db.query(RateLimit).filter(RateLimit.created_at < cutoff).delete()
    recent = db.query(RateLimit).filter(RateLimit.bucket == bucket, RateLimit.created_at >= cutoff).count()
    if recent >= limit:
        db.commit()
        return True
    db.add(RateLimit(bucket=bucket))
    db.commit()
    return False


def manage_links(subscriber: Subscriber) -> dict[str, str]:
    return {"manage": manage_url(subscriber), "unsubscribe": unsubscribe_url(subscriber)}
