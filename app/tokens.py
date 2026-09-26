"""Signed, passwordless links.

Every management link carries a signed payload with the subscriber id and their
current token version. Bumping ``Subscriber.token_version`` invalidates every link
previously issued for that reader, and a signature is scoped to one purpose, so a
verification link cannot be replayed as an unsubscribe link.
"""

from __future__ import annotations

from dataclasses import dataclass

from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy.orm import Session

from app.config import settings
from app.models import Subscriber

VERIFY = "verify"
MANAGE = "manage"

MAX_AGE = {
    VERIFY: 60 * 60 * 24 * 7,  # a week to click the verification mail
    MANAGE: 60 * 60 * 24 * 365,  # preference links live in old emails
}


class InvalidToken(Exception):
    pass


@dataclass
class TokenPayload:
    subscriber_id: str
    version: int


def _serializer(purpose: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.secret_key, salt=f"feather-press:{purpose}")


def make_token(subscriber: Subscriber, purpose: str) -> str:
    return _serializer(purpose).dumps({"sid": subscriber.id, "v": subscriber.token_version})


def read_token(token: str, purpose: str) -> TokenPayload:
    try:
        data = _serializer(purpose).loads(token, max_age=MAX_AGE[purpose])
    except SignatureExpired as exc:
        raise InvalidToken("This link has expired.") from exc
    except BadSignature as exc:
        raise InvalidToken("This link is not valid.") from exc
    return TokenPayload(subscriber_id=data["sid"], version=int(data["v"]))


def resolve(db: Session, token: str, purpose: str) -> Subscriber:
    payload = read_token(token, purpose)
    subscriber = db.get(Subscriber, payload.subscriber_id)
    if subscriber is None:
        raise InvalidToken("We could not find that subscription.")
    if payload.version != subscriber.token_version:
        raise InvalidToken("This link has been replaced by a newer one.")
    return subscriber


def manage_url(subscriber: Subscriber) -> str:
    return f"{settings.base_url}/preferences/{make_token(subscriber, MANAGE)}"


def unsubscribe_url(subscriber: Subscriber) -> str:
    return f"{settings.base_url}/unsubscribe/{make_token(subscriber, MANAGE)}"


def verify_url(subscriber: Subscriber) -> str:
    return f"{settings.base_url}/verify/{make_token(subscriber, VERIFY)}"
