"""Entrypoint for hosts that expect `main:app` at the repo root."""

from app.main import app

__all__ = ["app"]
