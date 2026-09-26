from __future__ import annotations

import uuid
from datetime import UTC, date, datetime

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(tz=UTC)


def new_id() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class ContentItem(Base):
    """A curated piece of content, seeded from content/library.json."""

    __tablename__ = "content_items"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    creator: Mapped[str] = mapped_column(String(200))
    publication: Mapped[str] = mapped_column(String(200))
    url: Mapped[str] = mapped_column(Text)
    fmt: Mapped[str] = mapped_column(String(20))  # article | essay | video
    topic: Mapped[str] = mapped_column(String(40))
    interests: Mapped[list[str]] = mapped_column(JSON, default=list)
    depth: Mapped[str] = mapped_column(String(20))  # accessible | deep
    duration_minutes: Mapped[int] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text)
    verified_on: Mapped[str] = mapped_column(String(20))

    @property
    def read_label(self) -> str:
        return {
            "article": "Read the article",
            "essay": "Read the essay",
            "video": "Watch the video",
        }.get(self.fmt, "Read the original")


class Subscriber(Base):
    __tablename__ = "subscribers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    # pending | active | paused | unsubscribed
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    depth: Mapped[str] = mapped_column(String(20), default="mix")
    topics: Mapped[list[str]] = mapped_column(JSON, default=list)
    interests: Mapped[list[str]] = mapped_column(JSON, default=list)
    token_version: Mapped[int] = mapped_column(Integer, default=1)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    editions: Mapped[list[Edition]] = relationship(back_populates="subscriber")

    @property
    def profile(self) -> dict:
        return {
            "topics": list(self.topics or []),
            "interests": list(self.interests or []),
            "depth": self.depth,
        }


class Edition(Base):
    """One newspaper edition: a preview, or a delivered daily email."""

    __tablename__ = "editions"
    __table_args__ = (UniqueConstraint("subscriber_id", "edition_date", name="uq_edition_per_day"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    subscriber_id: Mapped[str | None] = mapped_column(ForeignKey("subscribers.id"), nullable=True, index=True)
    edition_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    kind: Mapped[str] = mapped_column(String(20), default="preview")  # preview | daily
    status: Mapped[str] = mapped_column(String(20), default="preview")
    # preview | queued | sending | sent | failed | skipped
    profile_snapshot: Mapped[dict] = mapped_column(JSON, default=dict)
    intro: Mapped[str] = mapped_column(Text, default="")
    provider_message_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    provider: Mapped[str | None] = mapped_column(String(40), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    subscriber: Mapped[Subscriber | None] = relationship(back_populates="editions")
    items: Mapped[list[EditionItem]] = relationship(
        back_populates="edition", order_by="EditionItem.position", cascade="all, delete-orphan"
    )


class EditionItem(Base):
    __tablename__ = "edition_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    edition_id: Mapped[str] = mapped_column(ForeignKey("editions.id"), index=True)
    content_id: Mapped[str] = mapped_column(ForeignKey("content_items.id"))
    position: Mapped[int] = mapped_column(Integer)
    reason: Mapped[str] = mapped_column(Text)
    is_revisit: Mapped[bool] = mapped_column(Boolean, default=False)

    edition: Mapped[Edition] = relationship(back_populates="items")
    content: Mapped[ContentItem] = relationship()


class Feedback(Base):
    __tablename__ = "feedback"
    __table_args__ = (UniqueConstraint("subscriber_id", "content_id", name="uq_feedback"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    subscriber_id: Mapped[str] = mapped_column(ForeignKey("subscribers.id"), index=True)
    content_id: Mapped[str] = mapped_column(ForeignKey("content_items.id"))
    signal: Mapped[str] = mapped_column(String(10))  # more | less
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class RateLimit(Base):
    __tablename__ = "rate_limits"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    bucket: Mapped[str] = mapped_column(String(200), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
