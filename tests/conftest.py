from __future__ import annotations

import os
import tempfile
from collections.abc import Iterator

import pytest

os.environ.setdefault("SCHEDULER_ENABLED", "false")
os.environ.setdefault("EMAIL_PROVIDER", "console")
os.environ.setdefault("BASE_URL", "http://testserver")

_tmp = tempfile.mkdtemp(prefix="feather-tests-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["OUTBOX_DIR"] = f"{_tmp}/outbox"

from sqlalchemy.orm import Session  # noqa: E402

from app.content import seed_content  # noqa: E402
from app.db import SessionLocal, engine, init_db  # noqa: E402
from app.models import Base  # noqa: E402


@pytest.fixture()
def db() -> Iterator[Session]:
    Base.metadata.drop_all(bind=engine)
    init_db()
    session = SessionLocal()
    seed_content(session)
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db: Session):
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
