from __future__ import annotations

from datetime import datetime, timezone

from core.secure_codec import SecureCodec
from database.connection import Database
from models.passenger import BerthPreference, FoodPreference, Gender, Passenger


class PassengerRepository:
    def __init__(self, db: Database, codec: SecureCodec):
        self._db = db
        self._codec = codec

    def _row(self, row) -> Passenger:
        return Passenger(
            id=row["id"],
            name=self._codec.decode(row["name_enc"]),
            age=row["age"],
            gender=Gender(row["gender"]),
            berth_preference=BerthPreference(row["berth_preference"]),
            food_preference=FoodPreference(row["food_preference"]),
            include_in_booking=bool(row["include_in_booking"]),
        )

    def list(self) -> list[Passenger]:
        return [self._row(r) for r in self._db.query("SELECT * FROM passengers ORDER BY id")]

    def get(self, passenger_id: int) -> Passenger | None:
        row = self._db.query_one("SELECT * FROM passengers WHERE id = ?", (passenger_id,))
        return self._row(row) if row else None

    def add(self, p: Passenger) -> Passenger:
        now = datetime.now(timezone.utc).isoformat()
        cur = self._db.execute(
            "INSERT INTO passengers (name_enc, age, gender, berth_preference, food_preference,"
            " include_in_booking, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
            (self._codec.encode(p.name), p.age, p.gender.value, p.berth_preference.value,
             p.food_preference.value, int(p.include_in_booking), now, now),
        )
        return self.get(cur.lastrowid)  # type: ignore[return-value]

    def update(self, p: Passenger) -> Passenger:
        if p.id is None:
            raise ValueError("passenger has no id")
        now = datetime.now(timezone.utc).isoformat()
        self._db.execute(
            "UPDATE passengers SET name_enc=?, age=?, gender=?, berth_preference=?, food_preference=?,"
            " include_in_booking=?, updated_at=? WHERE id=?",
            (self._codec.encode(p.name), p.age, p.gender.value, p.berth_preference.value,
             p.food_preference.value, int(p.include_in_booking), now, p.id),
        )
        return self.get(p.id)  # type: ignore[return-value]

    def delete(self, passenger_id: int) -> bool:
        return self._db.execute("DELETE FROM passengers WHERE id = ?", (passenger_id,)).rowcount > 0
