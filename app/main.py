from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime

from fastapi import Depends, FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import APP_DIR, BASE_URL_CONFIGURED, settings
from app.content import (
    DEPTH_CHOICES,
    INTEREST_LABELS,
    TOPICS,
    TOPICS_BY_SLUG,
    interests_line,
    sample_edition,
    seed_content,
)
from app.db import SessionLocal, get_db, init_db
from app.emailer import active_provider, provider_is_live
from app.models import Edition, Feedback, Subscriber
from app.render import env, long_date
from app.scheduler import start_scheduler
from app.selection import Profile
from app.services import (
    ACTIVE,
    PAUSED,
    UNSUBSCRIBED,
    build_edition,
    local_now,
    rate_limited,
    run_daily,
    send_edition,
    send_verification,
    subscribe,
    verify,
)
from app.tokens import MANAGE, VERIFY, InvalidToken, manage_url, resolve, unsubscribe_url, verify_url


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    with SessionLocal() as db:
        seed_content(db)
    if settings.scheduler_enabled:
        start_scheduler()
    yield


app = FastAPI(title=settings.app_name, docs_url=None, redoc_url=None, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=str(APP_DIR / "static")), name="static")


@app.middleware("http")
async def learn_base_url(request: Request, call_next):
    # Links in email must point at whatever host this instance is actually served from,
    # unless BASE_URL pins it explicitly.
    if not BASE_URL_CONFIGURED:
        url = request.url.replace(path="", query="")
        forwarded = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
        scheme = forwarded or url.scheme
        if url.hostname not in ("localhost", "127.0.0.1", "testserver"):
            scheme = "https"  # bearer links must never travel in the clear
        settings.base_url = str(url.replace(scheme=scheme)).rstrip("/")
    return await call_next(request)


def render(name: str, **context) -> HTMLResponse:
    context.setdefault("provider", active_provider())
    context.setdefault("provider_is_live", provider_is_live())
    context.setdefault("dev_tools", settings.dev_tools_enabled)
    return HTMLResponse(env.get_template(name).render(**context))


def page_error(message: str, status_code: int = 400) -> HTMLResponse:
    return HTMLResponse(
        env.get_template("message.html").render(heading="That link didn't work", body=message),
        status_code=status_code,
    )


def client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


# ------------------------------------------------------------------------- onboarding


@app.get("/", response_class=HTMLResponse)
def landing() -> HTMLResponse:
    return render("landing.html", sample=sample_edition()["stories"])


@app.get("/sample", response_class=HTMLResponse)
def sample() -> HTMLResponse:
    """A fixed, clearly-labelled sample so people can read one before choosing."""
    return render("sample.html", sample=sample_edition())


@app.get("/start", response_class=HTMLResponse)
def start(edition: str | None = None, db: Session = Depends(get_db)) -> HTMLResponse:
    """`?edition=` comes back from a preview, so the form reopens on its choices."""
    previous = db.get(Edition, edition) if edition else None
    selected = Profile.from_dict(previous.profile_snapshot) if previous else None
    return render("onboarding.html", topics=TOPICS, depth_choices=DEPTH_CHOICES, selected=selected)


@app.post("/preview", response_class=HTMLResponse)
def create_preview(
    request: Request,
    topics: list[str] = Form(default=[]),
    interests: list[str] = Form(default=[]),
    depth: str = Form(default="mix"),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    if depth not in {d[0] for d in DEPTH_CHOICES}:
        depth = "mix"
    topics = [t for t in topics if t in TOPICS_BY_SLUG]
    if not topics:
        return render(
            "onboarding.html",
            topics=TOPICS,
            depth_choices=DEPTH_CHOICES,
            selected=Profile(topics=[], interests=interests, depth=depth),
            error="Choose at least one topic so we know where to start.",
        )
    allowed = {i.slug for t in topics for i in TOPICS_BY_SLUG[t].interests}
    interests = [i for i in interests if i in allowed]
    profile = Profile(topics=topics, interests=interests, depth=depth)
    edition = build_edition(db, profile)
    return RedirectResponse(f"/delivering/{edition.id}", status_code=303)


@app.get("/delivering/{edition_id}", response_class=HTMLResponse)
def delivering(edition_id: str, db: Session = Depends(get_db)) -> HTMLResponse:
    """Serius walking to the postbox. The edition below already exists; this is theatre."""
    edition = db.get(Edition, edition_id)
    if edition is None:
        return page_error("We couldn't find that edition. Choose your interests again to rebuild it.", 404)
    return render("delivery.html", next_url=f"/preview/{edition.id}")


@app.get("/preview/{edition_id}", response_class=HTMLResponse)
def show_preview(edition_id: str, db: Session = Depends(get_db)) -> HTMLResponse:
    edition = db.get(Edition, edition_id)
    if edition is None:
        return page_error("We couldn't find that edition. Choose your interests again to rebuild it.", 404)
    today = datetime.now(tz=UTC).date()
    profile = Profile.from_dict(edition.profile_snapshot)
    return render(
        "preview.html",
        edition=edition,
        items=edition.items,
        date_label=long_date(today),
        profile=profile,
        interest_labels=INTEREST_LABELS,
        interests_line=interests_line(profile),
    )


@app.post("/subscribe", response_class=HTMLResponse)
def do_subscribe(
    request: Request,
    edition_id: str = Form(...),
    email: str = Form(...),
    consent: str = Form(default=""),
    timezone_name: str = Form(default="UTC"),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    edition = db.get(Edition, edition_id)
    if edition is None or edition.subscriber_id is not None:
        return page_error("That preview expired. Choose your interests again.", 404)
    if consent != "yes":
        return page_error("We need your explicit consent before sending you email.", 400)
    email = email.strip().lower()
    if "@" not in email or len(email) < 5:
        return page_error("That email address doesn't look right.", 400)
    if rate_limited(db, f"subscribe:{client_key(request)}", limit=10) or rate_limited(
        db, f"subscribe-email:{email}", limit=5
    ):
        return page_error("Too many attempts just now. Try again in an hour.", 429)

    profile = Profile.from_dict(edition.profile_snapshot)
    subscriber, first_edition = subscribe(db, email, profile, timezone_name, edition)
    result = send_verification(db, subscriber, first_edition)
    return render(
        "check_email.html",
        subscriber=subscriber,
        result=result,
        verify_link=verify_url(subscriber) if settings.dev_tools_enabled else None,
    )


# ----------------------------------------------------------------------- verification


@app.get("/verify/{token}", response_class=HTMLResponse)
def verify_confirm(token: str, db: Session = Depends(get_db)) -> HTMLResponse:
    # A GET only shows a button: link scanners must not be able to activate a
    # subscription or trigger mail by fetching the URL.
    try:
        subscriber = resolve(db, token, VERIFY)
    except InvalidToken as exc:
        return page_error(str(exc), 400)
    return render("verify_confirm.html", subscriber=subscriber, token=token)


@app.post("/verify/{token}", response_class=HTMLResponse)
def verify_submit(request: Request, token: str, db: Session = Depends(get_db)) -> HTMLResponse:
    try:
        subscriber = resolve(db, token, VERIFY)
    except InvalidToken as exc:
        return page_error(str(exc), 400)
    if rate_limited(db, f"verify:{client_key(request)}", limit=20):
        return page_error("Too many attempts just now. Try again in an hour.", 429)

    from app.services import pending_first_edition_unsent

    already_active = subscriber.status == ACTIVE
    verify(db, subscriber)
    edition = pending_first_edition_unsent(db, subscriber)
    result = None
    if edition is not None:
        result = send_edition(db, edition)
    return render(
        "verified.html",
        subscriber=subscriber,
        result=result,
        already_active=already_active,
        manage_link=manage_url(subscriber),
        local_time=local_now(subscriber).strftime("%H:%M"),
    )


# ------------------------------------------------------------------------ preferences


@app.get("/preferences/{token}", response_class=HTMLResponse)
def preferences(token: str, saved: bool = False, db: Session = Depends(get_db)) -> HTMLResponse:
    try:
        subscriber = resolve(db, token, MANAGE)
    except InvalidToken as exc:
        return page_error(str(exc), 400)
    return render(
        "preferences.html",
        subscriber=subscriber,
        topics=TOPICS,
        depth_choices=DEPTH_CHOICES,
        token=token,
        saved=saved,
        unsubscribe_link=unsubscribe_url(subscriber),
    )


@app.post("/preferences/{token}", response_class=HTMLResponse)
def save_preferences(
    token: str,
    topics: list[str] = Form(default=[]),
    interests: list[str] = Form(default=[]),
    depth: str = Form(default="mix"),
    timezone_name: str = Form(default="UTC"),
    db: Session = Depends(get_db),
) -> HTMLResponse:
    try:
        subscriber = resolve(db, token, MANAGE)
    except InvalidToken as exc:
        return page_error(str(exc), 400)
    topics = [t for t in topics if t in TOPICS_BY_SLUG]
    if not topics:
        return page_error("Keep at least one topic, otherwise there is nothing to send.", 400)
    allowed = {i.slug for t in topics for i in TOPICS_BY_SLUG[t].interests}
    subscriber.topics = topics
    subscriber.interests = [i for i in interests if i in allowed]
    subscriber.depth = depth if depth in {d[0] for d in DEPTH_CHOICES} else "mix"
    subscriber.timezone = timezone_name or "UTC"
    db.commit()
    return RedirectResponse(f"/preferences/{token}?saved=1", status_code=303)


@app.post("/preferences/{token}/state", response_class=HTMLResponse)
def change_state(token: str, action: str = Form(...), db: Session = Depends(get_db)) -> HTMLResponse:
    try:
        subscriber = resolve(db, token, MANAGE)
    except InvalidToken as exc:
        return page_error(str(exc), 400)
    if action == "pause":
        subscriber.status = PAUSED
    elif action == "resume" and subscriber.status == PAUSED:
        subscriber.status = ACTIVE
    db.commit()
    return RedirectResponse(f"/preferences/{token}?saved=1", status_code=303)


@app.get("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_confirm(token: str, db: Session = Depends(get_db)) -> HTMLResponse:
    try:
        subscriber = resolve(db, token, MANAGE)
    except InvalidToken as exc:
        return page_error(str(exc), 400)
    return render("unsubscribe.html", subscriber=subscriber, token=token)


@app.post("/unsubscribe/{token}", response_class=HTMLResponse)
def unsubscribe_submit(token: str, db: Session = Depends(get_db)) -> HTMLResponse:
    try:
        subscriber = resolve(db, token, MANAGE)
    except InvalidToken as exc:
        return page_error(str(exc), 400)
    subscriber.status = UNSUBSCRIBED
    subscriber.unsubscribed_at = datetime.now(tz=UTC)
    db.commit()
    return render("message.html", heading="You're unsubscribed", body="No more editions will be sent.")


# --------------------------------------------------------------------------- feedback


@app.get("/feedback/{token}/{content_id}/{signal}", response_class=HTMLResponse)
def feedback_confirm(token: str, content_id: str, signal: str, db: Session = Depends(get_db)):
    try:
        subscriber = resolve(db, token, MANAGE)
    except InvalidToken as exc:
        return page_error(str(exc), 400)
    if signal not in ("more", "less"):
        return page_error("Unknown feedback signal.", 400)
    return render("feedback.html", subscriber=subscriber, token=token, content_id=content_id, signal=signal)


@app.post("/feedback/{token}/{content_id}/{signal}", response_class=HTMLResponse)
def feedback_submit(token: str, content_id: str, signal: str, db: Session = Depends(get_db)):
    try:
        subscriber = resolve(db, token, MANAGE)
    except InvalidToken as exc:
        return page_error(str(exc), 400)
    if signal not in ("more", "less"):
        return page_error("Unknown feedback signal.", 400)
    existing = db.scalars(
        select(Feedback).where(Feedback.subscriber_id == subscriber.id, Feedback.content_id == content_id)
    ).first()
    if existing is None:
        db.add(Feedback(subscriber_id=subscriber.id, content_id=content_id, signal=signal))
    else:
        existing.signal = signal
    db.commit()
    return render(
        "message.html",
        heading="Noted",
        body=(
            "We'll send more like that." if signal == "more" else "We'll steer away from that kind of piece."
        ),
    )


# ------------------------------------------------------------------------------ jobs


@app.post("/jobs/daily")
def daily_job(request: Request, force: bool = False, db: Session = Depends(get_db)) -> JSONResponse:
    secret = request.headers.get("x-cron-secret", "")
    if secret != settings.cron_secret:
        raise HTTPException(status_code=401, detail="bad cron secret")
    report = run_daily(db, force=force)
    return JSONResponse(report)


@app.get("/healthz")
def healthz(db: Session = Depends(get_db)) -> JSONResponse:
    from app.models import ContentItem

    return JSONResponse(
        {
            "ok": True,
            "content_items": db.query(ContentItem).count(),
            "subscribers": db.query(Subscriber).count(),
            "email_provider": active_provider(),
            "email_delivery_live": provider_is_live(),
        }
    )


# ------------------------------------------------------------- development-only tools


@app.get("/dev", response_class=HTMLResponse)
def dev_tools(db: Session = Depends(get_db)) -> HTMLResponse:
    if not settings.dev_tools_enabled:
        raise HTTPException(status_code=404)
    subscribers = db.scalars(select(Subscriber).order_by(Subscriber.created_at.desc())).all()
    rows = []
    for sub in subscribers:
        editions = db.scalars(
            select(Edition).where(Edition.subscriber_id == sub.id).order_by(Edition.created_at.desc())
        ).all()
        rows.append(
            {
                "subscriber": sub,
                "editions": editions,
                "manage": manage_url(sub),
                "verify": verify_url(sub),
            }
        )
    return render("dev.html", rows=rows, outbox=str(settings.outbox_dir))


@app.post("/dev/simulate", response_class=HTMLResponse)
def dev_simulate(subscriber_id: str = Form(...), db: Session = Depends(get_db)) -> HTMLResponse:
    if not settings.dev_tools_enabled:
        raise HTTPException(status_code=404)
    subscriber = db.get(Subscriber, subscriber_id)
    if subscriber is None:
        return page_error("No such subscriber.", 404)
    profile = Profile.from_dict(subscriber.profile)
    local_date = local_now(subscriber).date()
    existing = db.scalars(
        select(Edition).where(Edition.subscriber_id == subscriber.id, Edition.edition_date == local_date)
    ).first()
    if existing is not None:
        # Simulating "tomorrow" means a fresh edition, so free up today's slot marker.
        existing.edition_date = None
        db.commit()
    edition = build_edition(db, profile, subscriber=subscriber, kind="daily", status="queued")
    send_edition(db, edition)
    return RedirectResponse("/dev", status_code=303)


@app.post("/dev/reset", response_class=HTMLResponse)
def dev_reset(subscriber_id: str = Form(...), db: Session = Depends(get_db)) -> HTMLResponse:
    if not settings.dev_tools_enabled:
        raise HTTPException(status_code=404)
    subscriber = db.get(Subscriber, subscriber_id)
    if subscriber is None:
        return page_error("No such subscriber.", 404)
    for edition in db.scalars(select(Edition).where(Edition.subscriber_id == subscriber.id)).all():
        db.delete(edition)
    db.delete(subscriber)
    db.commit()
    return RedirectResponse("/dev", status_code=303)
