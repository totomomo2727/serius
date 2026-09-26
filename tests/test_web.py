from __future__ import annotations

from app.models import Edition, Subscriber
from app.tokens import MANAGE, VERIFY, make_token


def start_preview(client, topics=("ai",), interests=("ai-safety",), depth="mix"):
    response = client.post(
        "/preview",
        data={"topics": list(topics), "interests": list(interests), "depth": depth},
        follow_redirects=True,
    )
    assert response.status_code == 200
    return response


def test_landing_and_onboarding_render(client):
    landing = client.get("/")
    assert "A daily newspaper, curated for you." in landing.text
    start = client.get("/start")
    for label in ("Product design", "Philosophy", "Psychology", "AI", "Tech"):
        assert label in start.text
    assert "Your choices shape your daily edition." in start.text


def test_preview_requires_a_topic(client):
    response = client.post("/preview", data={"depth": "mix"}, follow_redirects=True)
    assert "at least one topic" in response.text


def test_preview_shows_three_real_pieces(client, db):
    response = start_preview(client)
    edition = db.query(Edition).order_by(Edition.created_at.desc()).first()
    assert len(edition.items) == 3
    for item in edition.items:
        assert item.content.title in response.text
        assert item.content.url in response.text
    assert "Why this one" in response.text


def test_subscribe_requires_consent(client, db):
    start_preview(client)
    edition = db.query(Edition).order_by(Edition.created_at.desc()).first()
    response = client.post("/subscribe", data={"edition_id": edition.id, "email": "a@example.com"})
    assert response.status_code == 400
    assert db.query(Subscriber).count() == 0


def test_full_journey_preview_to_first_edition(client, db):
    start_preview(client, topics=("tech",), interests=("software-craft",))
    edition = db.query(Edition).order_by(Edition.created_at.desc()).first()
    previewed = [i.content_id for i in edition.items]

    response = client.post(
        "/subscribe",
        data={
            "edition_id": edition.id,
            "email": "journey@example.com",
            "consent": "yes",
            "timezone_name": "Europe/Berlin",
        },
    )
    assert response.status_code == 200
    subscriber = db.query(Subscriber).filter_by(email="journey@example.com").one()
    assert subscriber.status == "pending"

    token = make_token(subscriber, VERIFY)
    # Loading the link must not activate anything: scanners only ever issue GETs.
    assert client.get(f"/verify/{token}").status_code == 200
    db.refresh(subscriber)
    assert subscriber.status == "pending"

    confirmed = client.post(f"/verify/{token}")
    assert confirmed.status_code == 200
    db.expire_all()
    assert subscriber.status == "active"

    first = db.query(Edition).filter_by(subscriber_id=subscriber.id, kind="first").one()
    assert first.status == "sent"
    assert [i.content_id for i in first.items] == previewed


def test_preferences_pause_and_unsubscribe(client, db):
    subscriber = Subscriber(
        email="prefs@example.com", topics=["ai"], interests=[], depth="mix", status="active"
    )
    db.add(subscriber)
    db.commit()
    token = make_token(subscriber, MANAGE)

    page = client.get(f"/preferences/{token}")
    assert "Your preferences" in page.text

    client.post(
        f"/preferences/{token}",
        data={
            "topics": ["philosophy"],
            "interests": ["stoicism"],
            "depth": "deep",
            "timezone_name": "Asia/Tokyo",
        },
        follow_redirects=False,
    )
    db.refresh(subscriber)
    assert subscriber.topics == ["philosophy"]
    assert subscriber.interests == ["stoicism"]
    assert subscriber.depth == "deep"
    assert subscriber.timezone == "Asia/Tokyo"

    client.post(f"/preferences/{token}/state", data={"action": "pause"}, follow_redirects=False)
    db.refresh(subscriber)
    assert subscriber.status == "paused"
    client.post(f"/preferences/{token}/state", data={"action": "resume"}, follow_redirects=False)
    db.refresh(subscriber)
    assert subscriber.status == "active"

    assert client.get(f"/unsubscribe/{token}").status_code == 200
    db.refresh(subscriber)
    assert subscriber.status == "active"  # a GET must not unsubscribe
    client.post(f"/unsubscribe/{token}")
    db.refresh(subscriber)
    assert subscriber.status == "unsubscribed"


def test_bad_token_is_rejected(client):
    assert client.get("/preferences/not-a-real-token").status_code == 400


def test_cron_endpoint_requires_the_secret(client):
    assert client.post("/jobs/daily").status_code == 401
    from app.config import settings

    ok = client.post("/jobs/daily", headers={"x-cron-secret": settings.cron_secret})
    assert ok.status_code == 200
    assert "considered" in ok.json()


def test_health_reports_library_and_provider(client):
    body = client.get("/healthz").json()
    assert body["content_items"] == 75
    assert body["email_provider"] == "console"
    assert body["email_delivery_live"] is False
