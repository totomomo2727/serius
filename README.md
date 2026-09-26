# The Feather Press

A daily newspaper, curated for you. Three thoughtful articles, essays, or videos chosen
around your interests, summarised so the substance takes three minutes, with the originals
one link away.

The email is the product. The web app exists to choose interests, see a real edition before
subscribing, and manage delivery afterwards — no password, no dashboard.

## The flow

1. `/` introduces the paper, `/start` collects broad topics, optional specific interests, and
   preferred depth.
2. `/preview/{id}` runs the real recommendation engine and shows the edition that would be
   sent, after a short animation of Serius walking up to a postbox and posting the letter
   (skipped entirely under `prefers-reduced-motion`).
3. Subscribing stores the reader and **pins that exact edition** as their first one, then
   emails a confirmation link.
4. Confirming (a POST, so link scanners cannot subscribe anyone) sends that same edition
   immediately.
5. An hourly job sends a new edition to each reader once 08:00 has passed in their own
   timezone, once per local day.

## Running it

```bash
uv venv --python 3.12 .venv
uv pip install --python .venv/bin/python -e ".[dev]"
.venv/bin/uvicorn app.main:app --reload
```

Then open http://localhost:8000. The content library is seeded into SQLite on startup.

```bash
.venv/bin/python -m pytest -q     # tests
.venv/bin/ruff check . && .venv/bin/ruff format --check .
```

## Environment variables

| Variable | Default | Notes |
| --- | --- | --- |
| `BASE_URL` | auto-detected per request | Pin it in production so email links are absolute and stable. |
| `DATABASE_URL` | `sqlite:///data/feather.db` | Any SQLAlchemy URL. The SQLite file must live on persistent storage. |
| `SECRET_KEY` | random, persisted to `data/secrets.json` | Signs verification/management links. Set explicitly when running more than one instance. |
| `CRON_SECRET` | random, persisted to `data/secrets.json` | Required in the `X-Cron-Secret` header on `POST /jobs/daily`. |
| `RESEND_API_KEY` | empty | When empty, nothing is delivered: messages are written to `data/outbox/` and clearly marked undelivered. |
| `EMAIL_PROVIDER` | `auto` | `auto` (Resend when a key exists, otherwise console), `resend`, or `console`. |
| `EMAIL_FROM` | `The Feather Press <onboarding@resend.dev>` | Resend's shared sender only reaches your own account address; use a verified domain for real readers. |
| `EMAIL_ALLOWED_RECIPIENTS` | empty | Comma-separated allow-list. Set it while testing so mail can only reach the test inbox. |
| `DELIVERY_HOUR_LOCAL` | `8` | Local hour at which an edition becomes due. |
| `SCHEDULER_ENABLED` | `true` | Turn off when the platform provides cron and calls `/jobs/daily`. |
| `DEV_TOOLS_ENABLED` | `true` | **Set to `false` in production.** Hides `/dev` and the confirmation-link shortcut. |
| `OUTBOX_DIR` | `data/outbox` | Where console messages are written. |

## Deployment requirements

- Persistent disk for `data/` (SQLite database, generated secrets, outbox).
- `RESEND_API_KEY` plus a verified sending domain for `EMAIL_FROM`; without a verified
  domain Resend only delivers to the account owner's own address.
- Either keep `SCHEDULER_ENABLED=true` on a single always-on instance, or run platform cron
  hourly: `curl -X POST -H "X-Cron-Secret: $CRON_SECRET" $BASE_URL/jobs/daily`.
- `DEV_TOOLS_ENABLED=false` and an explicit `BASE_URL`.
- `GET /healthz` reports library size, subscriber count, and whether delivery is live.

## The content library

`content/library/*.json` holds 75 hand-checked pieces across product design, philosophy,
psychology, AI, and tech. Each record carries a stable id, title, creator, publication or
channel, url, format (`article` / `essay` / `video`), broad topic, specific-interest tags,
depth (`accessible` / `deep`), estimated minutes, an original 130–170 word summary, and the
date it was verified.

To add a piece: read or watch it, append a record to the right file with a new stable id,
use only interest slugs listed in `app/content.py`, and write the summary yourself. Restart
the app (or call `seed_content`) to upsert it — seeding is idempotent and keyed by id, so
editing a record updates it in place and nothing is ever duplicated. Summaries are checked
at load time for required fields, known topics, known interests, and valid formats.

## How selection works

`app/selection.py` scores each piece: a specific-interest match is worth 10, the broad topic
4, matching the preferred depth 3, and reader feedback ±5/8. Variety penalties nudge the
edition away from repeating a format, topic, or creator, but never override a substantially
stronger match. Ties break on id, so the same profile always yields the same edition.

Pieces the reader has already been sent are excluded while suitable unseen content remains.
Once it runs out, the least-recently-sent relevant pieces return, labelled as revisits in
both the reason line and the edition itself. Every edition stores the profile used, the
three items, and the reason for each, so a preview, its email, and any retry are identical.
