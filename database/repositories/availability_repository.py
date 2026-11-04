from __future__ import annotations

from datetime import datetime

from database.connection import Database
from models.availability import AvailabilityQuery, AvailabilityResult, AvailabilityStatus


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class AvailabilityRepository:
    def __init__(self, db: Database, max_rows_per_query: int = 2000):
        self._db = db
        self._max_rows = max_rows_per_query

    def add(self, result: AvailabilityResult, changed: bool) -> AvailabilityResult:
        cur = self._db.execute(
            "INSERT INTO availability_history (query_key, status, seats, raw_text, source, fetched_at,"
            " source_timestamp, is_cached, confirmed_by_source, simulated, changed)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (result.query.key, result.status.value, result.seats, result.raw_text, result.source,
             result.fetched_at.isoformat(),
             result.source_timestamp.isoformat() if result.source_timestamp else None,
             int(result.is_cached), int(result.confirmed_by_source), int(result.simulated), int(changed)),
        )
        self._db.execute(
            "DELETE FROM availability_history WHERE query_key = ? AND id NOT IN ("
            " SELECT id FROM availability_history WHERE query_key = ? ORDER BY id DESC LIMIT ?)",
            (result.query.key, result.query.key, self._max_rows),
        )
        return AvailabilityResult(**{**result.__dict__, "id": cur.lastrowid})

    def history(self, query: AvailabilityQuery, limit: int = 200, changes_only: bool = False) -> list[tuple[AvailabilityResult, bool]]:
        sql = "SELECT * FROM availability_history WHERE query_key = ?"
        if changes_only:
            sql += " AND changed = 1"
        sql += " ORDER BY id DESC LIMIT ?"
        rows = self._db.query(sql, (query.key, limit))
        return [
            (
                AvailabilityResult(
                    id=r["id"], query=query, status=AvailabilityStatus(r["status"]), seats=r["seats"],
                    raw_text=r["raw_text"], source=r["source"], fetched_at=_dt(r["fetched_at"]),
                    source_timestamp=_dt(r["source_timestamp"]), is_cached=bool(r["is_cached"]),
                    confirmed_by_source=bool(r["confirmed_by_source"]), simulated=bool(r["simulated"]),
                ),
                bool(r["changed"]),
            )
            for r in rows
        ]

    def latest(self, query: AvailabilityQuery) -> AvailabilityResult | None:
        rows = self.history(query, limit=1)
        return rows[0][0] if rows else None
