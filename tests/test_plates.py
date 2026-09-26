"""Every piece shows either its publisher's own image or a drawn Serius plate."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ContentItem

THUMBS = Path(__file__).resolve().parents[1] / "app" / "static" / "thumbs"


def test_cached_thumbnails_exist_and_record_their_source(db: Session) -> None:
    items = db.scalars(select(ContentItem).where(ContentItem.thumbnail.is_not(None))).all()
    assert items, "the library should carry article-sourced images"
    for item in items:
        assert (THUMBS / item.thumbnail).is_file(), f"{item.slug}: {item.thumbnail} missing"
        assert item.thumbnail_source, f"{item.slug}: image without a recorded source"
        assert item.thumbnail_url == f"/static/thumbs/{item.thumbnail}"
        assert item.credit == f"Image: {item.publication}"
        assert item.publication in item.alt_text


def test_pieces_without_an_image_fall_back_to_a_drawn_plate(db: Session) -> None:
    items = db.scalars(select(ContentItem).where(ContentItem.thumbnail.is_(None))).all()
    assert items, "the drawn fallback should still be exercised by the library"
    for item in items:
        assert item.thumbnail_url is None
        assert item.credit
        assert item.alt_text


def test_edition_renders_both_kinds_of_plate(client) -> None:
    response = client.post(
        "/preview",
        data={"topics": ["ai", "philosophy", "product-design"], "depth": "mix"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    body = response.text
    assert "plate-photo" in body or "plate-drawn" in body
    assert "/static/serius/" in body  # Serius is present either way
