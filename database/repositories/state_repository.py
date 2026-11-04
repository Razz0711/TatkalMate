"""Readiness confirmations and small key/value application state."""

from __future__ import annotations

from datetime import datetime

from database.connection import Database


class StateRepository:
    def __init__(self, db: Database):
        self._db = db

    def confirm(self, item: str, context_key: str, when: datetime) -> None:
        self._db.execute(
            "INSERT INTO readiness_confirmations (item, context_key, confirmed_at) VALUES (?,?,?)"
            " ON CONFLICT(item) DO UPDATE SET context_key=excluded.context_key, confirmed_at=excluded.confirmed_at",
            (item, context_key, when.isoformat()),
        )

    def unconfirm(self, item: str) -> None:
        self._db.execute("DELETE FROM readiness_confirmations WHERE item = ?", (item,))

    def confirmation(self, item: str) -> tuple[str, datetime] | None:
        row = self._db.query_one(
            "SELECT context_key, confirmed_at FROM readiness_confirmations WHERE item = ?", (item,)
        )
        return (row["context_key"], datetime.fromisoformat(row["confirmed_at"])) if row else None

    def get(self, key: str, default: str | None = None) -> str | None:
        row = self._db.query_one("SELECT value FROM app_state WHERE key = ?", (key,))
        return row["value"] if row else default

    def set(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO app_state (key, value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM app_state WHERE key = ?", (key,))
