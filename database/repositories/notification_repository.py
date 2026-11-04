from __future__ import annotations

from datetime import datetime

from database.connection import Database
from models.notification import Notification, NotificationKind, Severity


class NotificationRepository:
    def __init__(self, db: Database, keep: int = 1000):
        self._db = db
        self._keep = keep

    @staticmethod
    def _row(r) -> Notification:
        return Notification(
            id=r["id"], kind=NotificationKind(r["kind"]), severity=Severity(r["severity"]),
            title=r["title"], body=r["body"], dedupe_key=r["dedupe_key"],
            created_at=datetime.fromisoformat(r["created_at"]), read=bool(r["read"]),
        )

    def add(self, n: Notification) -> Notification:
        cur = self._db.execute(
            "INSERT INTO notifications (kind, severity, title, body, dedupe_key, created_at, read)"
            " VALUES (?,?,?,?,?,?,0)",
            (n.kind.value, n.severity.value, n.title, n.body, n.dedupe_key, n.created_at.isoformat()),
        )
        self._db.execute(
            "DELETE FROM notifications WHERE id NOT IN (SELECT id FROM notifications ORDER BY id DESC LIMIT ?)",
            (self._keep,),
        )
        return Notification(**{**n.__dict__, "id": cur.lastrowid})

    def last_created_at(self, dedupe_key: str) -> datetime | None:
        row = self._db.query_one(
            "SELECT created_at FROM notifications WHERE dedupe_key = ? ORDER BY id DESC LIMIT 1", (dedupe_key,)
        )
        return datetime.fromisoformat(row["created_at"]) if row else None

    def recent(self, limit: int = 200) -> list[Notification]:
        return [self._row(r) for r in self._db.query("SELECT * FROM notifications ORDER BY id DESC LIMIT ?", (limit,))]

    def unread_count(self) -> int:
        return self._db.query_one("SELECT COUNT(*) AS c FROM notifications WHERE read = 0")["c"]

    def mark_read(self, notification_id: int | None = None) -> None:
        if notification_id is None:
            self._db.execute("UPDATE notifications SET read = 1 WHERE read = 0")
        else:
            self._db.execute("UPDATE notifications SET read = 1 WHERE id = ?", (notification_id,))

    def clear(self) -> None:
        self._db.execute("DELETE FROM notifications")
