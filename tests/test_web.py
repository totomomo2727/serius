from __future__ import annotations

from app.models import Edition, Subscriber
from app.tokens import MANAGE, make_token


def start_preview(client, topics=("ai",), interests=("ai-safety",), depth="mix"):
    """Post the interests, walk through the delivery screen, read the edition."""
    created = client.post(
        "/preview",
        data={"topics": list(topics), "interests": list(interests), "depth": depth},
        follow_redirects=False,
    )
    assert created.status_code == 303
    delivering = client.get(created.headers["location"])
    assert delivering.status_code == 200
    response = client.get(delivering.text.split('data-next="')[1].split('"')[0])
    assert response.status_code == 200
    return response


def test_landing_and_onboarding_render(client):
    landing = client.get("/")
    assert "Worth your attention." in landing.text
    assert "Find my daily three" in landing.text
    start = client.get("/start")
    for label in ("Product design", "Philosophy", "Psychology", "AI", "Tech"):
        assert label in start.text
    assert "curious about?" in start.text


def test_landing_content_is_present_before_any_motion_runs(client):
    """Revealed elements must be in the markup, not created by the script."""
    landing = client.get("/")
    for marker in ('data-reveal="paper"', 'data-reveal="bird"', "data-stagger"):
        assert marker in landing.text
    assert "a small bird." in landing.text
    assert "data-sound-toggle" in landing.text
    assert 'aria-pressed="false"' in landing.text


def test_three_sample_editions_are_offered_with_the_featured_one_first(client):
    from app.content import sample_editions

    samples = sample_editions()
    assert len(samples) == 3
    assert samples[0]["slug"] == "philosophy-psychology"
    assert len({s["slug"] for s in samples}) == 3
    # Different readers must be shown genuinely different finds.
    picks = [{story["url"] for story in s["stories"]} for s in samples]
    assert picks[0] & picks[1] == set()
    assert picks[0] & picks[2] == set()
    assert picks[1] & picks[2] == set()

    for page in ("/", "/sample"):
        text = client.get(page).text
        first = text.index('data-sample="philosophy-psychology"')
        for sample in samples:
            assert first <= text.index(f'data-sample="{sample["slug"]}"')

    # Every sheet in the deck is its own control; no separate tabs to click.
    landing = client.get("/").text
    assert "data-sample-stack" in landing
    assert landing.count('class="sample-card"') == 3
    assert "data-sample-tab" not in landing

    sample_page = client.get("/sample").text
    for story in samples[2]["stories"]:
        assert story["url"] in sample_page
        assert story["title"] in sample_page


def test_adjusting_interests_reopens_the_form_on_the_same_choices(client, db):
    start_preview(client, topics=("philosophy",), interests=("ethics",), depth="deep")
    edition = db.query(Edition).order_by(Edition.created_at.desc()).first()
    form = client.get(f"/start?edition={edition.id}")
    assert 'value="philosophy" data-topic="philosophy" checked' in form.text.replace("\n", " ")
    assert 'value="ethics"' in form.text
    assert 'value="deep"' in form.text


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
    assert "— Serius" in response.text


def test_preview_offers_the_delivery_invitation_without_scripting(client, db):
    response = start_preview(client)
    # The pop-up carries the daily promise, and the tail below it still works
    # for anyone the script never reaches.
    assert "data-delivery-modal" in response.text
    assert "data-edition-end" in response.text
    assert "searches the web all day" in response.text.lower()
    assert response.text.count('action="/subscribe"') == 2
    assert 'id="email-page"' in response.text
    assert 'id="email-modal"' in response.text


def test_subscribe_rejects_an_unusable_address(client, db):
    start_preview(client)
    edition = db.query(Edition).order_by(Edition.created_at.desc()).first()
    response = client.post("/subscribe", data={"edition_id": edition.id, "email": "nope"})
    assert response.status_code == 400
    assert db.query(Subscriber).count() == 0


def test_subscribing_a_known_address_changes_nothing(client, db):
    start_preview(client, topics=("tech",), interests=("software-craft",))
    first = db.query(Edition).order_by(Edition.created_at.desc()).first()
    client.post("/subscribe", data={"edition_id": first.id, "email": "reader@example.com"})
    db.expire_all()
    subscriber = db.query(Subscriber).filter_by(email="reader@example.com").one()
    topics = list(subscriber.topics)

    # A stranger typing that address must not rewrite the reader's interests or
    # be handed their management link.
    start_preview(client, topics=("philosophy",), interests=("ethics",))
    other = db.query(Edition).order_by(Edition.created_at.desc()).first()
    response = client.post(
        "/subscribe", data={"edition_id": other.id, "email": "reader@example.com"}
    )
    assert response.status_code == 200
    assert "/manage/" not in response.text
    db.expire_all()
    subscriber = db.query(Subscriber).filter_by(email="reader@example.com").one()
    assert list(subscriber.topics) == topics
    assert db.query(Edition).filter_by(subscriber_id=subscriber.id).count() == 1


def test_full_journey_preview_to_first_edition(client, db):
    start_preview(client, topics=("tech",), interests=("software-craft",))
    edition = db.query(Edition).order_by(Edition.created_at.desc()).first()
    previewed = [i.content_id for i in edition.items]

    response = client.post(
        "/subscribe",
        data={
            "edition_id": edition.id,
            "email": "journey@example.com",
            "timezone_name": "Europe/Berlin",
        },
    )
    # One step: the address alone subscribes them and sends what they just read.
    assert response.status_code == 200
    db.expire_all()
    subscriber = db.query(Subscriber).filter_by(email="journey@example.com").one()
    assert subscriber.status == "active"
    assert subscriber.timezone == "Europe/Berlin"
    assert "journey@example.com" in response.text

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
    assert body["content_items"] == 88
    assert body["email_provider"] == "console"
    assert body["email_delivery_live"] is False
