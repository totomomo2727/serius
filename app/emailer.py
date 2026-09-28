"""Transactional email sending.

Two providers:

* ``resend`` — real delivery through https://resend.com, used when RESEND_API_KEY is set.
* ``console`` — writes the exact message to ``data/outbox/`` and returns a local id.
  Nothing is delivered. The app never reports console writes as delivered mail.

``EMAIL_ALLOWED_RECIPIENTS`` (comma separated) restricts real sending to a test
inbox; anything else fails loudly rather than mailing a stranger.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

from app.config import settings

RESEND_ENDPOINT = "https://api.resend.com/emails"


@dataclass
class SendResult:
    ok: bool
    provider: str
    message_id: str | None = None
    error: str | None = None
    delivered: bool = False  # False for console writes, even when ok


def active_provider() -> str:
    if settings.email_provider != "auto":
        return settings.email_provider
    return "resend" if settings.resend_api_key else "console"


def provider_is_live() -> bool:
    return active_provider() != "console"


def allowed_recipients() -> list[str]:
    raw = settings.email_allowed_recipients.strip()
    return [a.strip().lower() for a in raw.split(",") if a.strip()]


def recipient_allowed(to: str) -> bool:
    allow = allowed_recipients()
    return not allow or to.strip().lower() in allow


def send_email(
    to: str,
    subject: str,
    html: str,
    text: str,
    headers: dict[str, str] | None = None,
) -> SendResult:
    provider = active_provider()
    if provider != "console" and not recipient_allowed(to):
        return SendResult(
            ok=False,
            provider=provider,
            error=(
                f"{to} is not in EMAIL_ALLOWED_RECIPIENTS; refusing to send while the allow-list is active."
            ),
        )
    if provider == "resend":
        return _send_resend(to, subject, html, text, headers or {})
    return _write_console(to, subject, html, text, headers or {})


def _send_resend(to: str, subject: str, html: str, text: str, headers: dict[str, str]) -> SendResult:
    payload: dict = {
        "from": settings.email_from,
        "to": [to],
        "subject": subject,
        "html": html,
        "text": text,
    }
    if settings.email_reply_to:
        payload["reply_to"] = settings.email_reply_to
    if headers:
        payload["headers"] = headers
    try:
        response = httpx.post(
            RESEND_ENDPOINT,
            json=payload,
            headers={"Authorization": f"Bearer {settings.resend_api_key}"},
            timeout=20.0,
        )
    except httpx.HTTPError as exc:
        return SendResult(ok=False, provider="resend", error=f"network error: {exc}")

    if response.status_code >= 400:
        detail = response.text[:500]
        return SendResult(
            ok=False, provider="resend", error=f"resend returned {response.status_code}: {detail}"
        )
    try:
        message_id = response.json().get("id")
    except ValueError:
        message_id = None
    return SendResult(ok=True, provider="resend", message_id=message_id, delivered=True)


def _write_console(to: str, subject: str, html: str, text: str, headers: dict[str, str]) -> SendResult:
    outbox = settings.outbox_dir
    outbox.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%dT%H%M%S")
    message_id = f"console-{stamp}-{uuid.uuid4().hex[:8]}"
    (outbox / f"{message_id}.html").write_text(html)
    (outbox / f"{message_id}.txt").write_text(text)
    (outbox / f"{message_id}.json").write_text(
        json.dumps(
            {
                "to": to,
                "from": settings.email_from,
                "subject": subject,
                "headers": headers,
                "written_at": stamp,
            },
            indent=2,
        )
    )
    return SendResult(ok=True, provider="console", message_id=message_id, delivered=False)
