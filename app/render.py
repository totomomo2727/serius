"""Jinja environment plus HTML/plain-text rendering for both web pages and email."""

from __future__ import annotations

from datetime import date

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import APP_DIR, settings
from app.content import TOPIC_LABELS, interests_line
from app.models import Edition
from app.selection import Profile
from app.tokens import MANAGE, make_token, manage_url, unsubscribe_url, verify_url

TEMPLATE_DIR = APP_DIR / "templates"

env = Environment(
    loader=FileSystemLoader(str(TEMPLATE_DIR)),
    autoescape=select_autoescape(["html"]),
    trim_blocks=True,
    lstrip_blocks=True,
)


def _static_version() -> str:
    """Newest stylesheet/script mtime, so a deploy never serves a stale pairing."""
    press = APP_DIR / "static" / "press"
    stamps = [p.stat().st_mtime for p in press.glob("*.css")]
    stamps += [p.stat().st_mtime for p in press.glob("*.js")]
    return str(int(max(stamps))) if stamps else "1"


env.globals.update(
    app_name=settings.app_name,
    tagline=settings.tagline,
    static_version=_static_version(),
)


def long_date(value: date) -> str:
    return f"{value.strftime('%A')}, {value.strftime('%-d')} {value.strftime('%B %Y')}"


def topic_label(slug: str) -> str:
    return TOPIC_LABELS.get(slug, slug.replace("-", " "))


env.filters["long_date"] = long_date
env.filters["topic_label"] = topic_label


def asset_base() -> str:
    """Where emailed images are fetched from; images need a host the inbox can reach."""
    return (settings.asset_base_url or settings.base_url).rstrip("/")


def edition_context(edition: Edition, local_date: date) -> dict:
    subscriber = edition.subscriber
    return {
        "edition": edition,
        "items": edition.items,
        "local_date": local_date,
        "date_label": long_date(local_date),
        "subscriber": subscriber,
        "manage_link": manage_url(subscriber) if subscriber else "",
        "manage_token": make_token(subscriber, MANAGE) if subscriber else "",
        "unsubscribe_link": unsubscribe_url(subscriber) if subscriber else "",
        "base_url": settings.base_url,
        "asset_url": asset_base(),
        "interests_line": interests_line(Profile.from_dict(edition.profile_snapshot)),
    }


def render_edition_email(edition: Edition, local_date: date) -> tuple[str, str, str]:
    """Return (subject, html, text)."""
    context = edition_context(edition, local_date)
    subject = f"Your edition — {long_date(local_date)}"
    html = env.get_template("email/edition.html").render(**context)
    text = env.get_template("email/edition.txt").render(**context)
    return subject, html, text


def render_verification_email(subscriber, preview_items) -> tuple[str, str, str]:
    context = {
        "subscriber": subscriber,
        "items": preview_items,
        "verify_link": verify_url(subscriber),
        "base_url": settings.base_url,
        "asset_url": asset_base(),
    }
    subject = "Confirm your subscription to The Feather Press"
    html = env.get_template("email/verify.html").render(**context)
    text = env.get_template("email/verify.txt").render(**context)
    return subject, html, text
