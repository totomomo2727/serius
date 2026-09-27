"""Every piece shows an image taken from the source it links to."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ContentItem

THUMBS = Path(__file__).resolve().parents[1] / "app" / "static" / "thumbs"


def test_every_piece_carries_an_image_from_its_source(db: Session) -> None:
    items = db.scalars(select(ContentItem)).all()
    assert items
    for item in items:
        assert item.thumbnail, f"{item.slug}: no image taken from the source"
        assert (THUMBS / item.thumbnail).is_file(), f"{item.slug}: {item.thumbnail} missing"
        assert item.thumbnail_source, f"{item.slug}: image without a recorded source"
        assert item.thumbnail_kind in ("image", "page"), f"{item.slug}: {item.thumbnail_kind}"
        assert item.thumbnail_url == f"/static/thumbs/{item.thumbnail}"
        assert item.credit
        assert item.alt_text


def test_credits_distinguish_a_page_capture_from_a_published_image(db: Session) -> None:
    for item in db.scalars(select(ContentItem)).all():
        if item.thumbnail_credit:
            assert item.credit == item.thumbnail_credit
        elif item.thumbnail_kind == "page":
            assert item.credit == f"From the page at {item.publication}"
        else:
            assert item.credit == f"Image: {item.publication}"
        assert item.publication in item.alt_text


def test_a_piece_without_an_image_still_falls_back_to_a_drawn_plate() -> None:
    item = ContentItem(publication="Nowhere", fmt="essay", title="Untitled")
    assert item.thumbnail_url is None
    assert item.credit
    assert item.alt_text


def test_edition_renders_source_imagery(client) -> None:
    response = client.post(
        "/preview",
        data={"topics": ["ai", "philosophy", "product-design"], "depth": "mix"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    body = response.text
    assert "plate-photo" in body
    assert "plate-drawn" not in body  # every piece brings its own image
    assert "/static/serius/" in body  # Serius still keeps the margins
