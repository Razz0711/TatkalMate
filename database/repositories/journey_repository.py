from __future__ import annotations

from datetime import date, datetime, timezone

from database.connection import Database
from models.journey import Journey, TatkalCategory, TravelClass


class JourneyRepository:
    def __init__(self, db: Database):
        self._db = db

    @staticmethod
    def _row(row) -> Journey:
        return Journey(
            id=row["id"],
            train_number=row["train_number"],
            train_name=row["train_name"],
            from_station=row["from_station"],
            to_station=row["to_station"],
            journey_date=date.fromisoformat(row["journey_date"]),
            travel_class=TravelClass(row["travel_class"]),
            category=TatkalCategory(row["category"]),
            boarding_day_offset=row["boarding_day_offset"],
        )

    def get_active(self) -> Journey | None:
        row = self._db.query_one("SELECT * FROM journeys WHERE is_active = 1 ORDER BY id DESC LIMIT 1")
        return self._row(row) if row else None

    def save_active(self, j: Journey) -> Journey:
        now = datetime.now(timezone.utc).isoformat()
        with self._db.transaction() as conn:
            conn.execute("UPDATE journeys SET is_active = 0 WHERE is_active = 1")
            cur = conn.execute(
                "INSERT INTO journeys (train_number, train_name, from_station, to_station, journey_date,"
                " travel_class, category, boarding_day_offset, is_active, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?,?,?,1,?,?)",
                (j.train_number, j.train_name, j.from_station, j.to_station, j.journey_date.isoformat(),
                 j.travel_class.value, j.category.value, j.boarding_day_offset, now, now),
            )
            new_id = cur.lastrowid
        row = self._db.query_one("SELECT * FROM journeys WHERE id = ?", (new_id,))
        return self._row(row)

    def clear_active(self) -> None:
        self._db.execute("UPDATE journeys SET is_active = 0 WHERE is_active = 1")
