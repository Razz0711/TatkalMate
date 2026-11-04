"""Passenger profile management.

Names are encrypted at rest (DPAPI on Windows) and registered with the log
redactor so they never appear in log files. Logs refer to passengers by id.
"""

from __future__ import annotations

import hashlib
import logging

from core.event_bus import EventBus, EventType
from core.logging_config import REDACTOR
from database.repositories.passenger_repository import PassengerRepository
from models.passenger import MAX_TATKAL_PASSENGERS, Passenger

log = logging.getLogger(__name__)


class PassengerService:
    def __init__(self, repo: PassengerRepository, bus: EventBus):
        self._repo = repo
        self._bus = bus
        self._register_names(self._repo.list())

    def _register_names(self, passengers: list[Passenger]) -> None:
        for p in passengers:
            REDACTOR.register(p.name, *p.name.split())

    def list(self) -> list[Passenger]:
        return self._repo.list()

    def booking_passengers(self) -> list[Passenger]:
        return [p for p in self._repo.list() if p.include_in_booking]

    def add(self, passenger: Passenger) -> Passenger:
        p = passenger.validate()
        self._register_names([p])
        saved = self._repo.add(p)
        log.info("passenger added", extra={"passenger_id": saved.id})
        self._bus.publish(EventType.PASSENGERS_CHANGED)
        return saved

    def update(self, passenger: Passenger) -> Passenger:
        if passenger.id is None:
            raise ValueError("passenger has no id")
        p = passenger.validate()
        self._register_names([p])
        saved = self._repo.update(p)
        log.info("passenger updated", extra={"passenger_id": saved.id})
        self._bus.publish(EventType.PASSENGERS_CHANGED)
        return saved

    def delete(self, passenger_id: int) -> bool:
        deleted = self._repo.delete(passenger_id)
        if deleted:
            log.info("passenger deleted", extra={"passenger_id": passenger_id})
            self._bus.publish(EventType.PASSENGERS_CHANGED)
        return deleted

    def warnings(self) -> list[str]:
        selected = self.booking_passengers()
        out = []
        if not selected:
            out.append("No passenger is selected for the booking.")
        if len(selected) > MAX_TATKAL_PASSENGERS:
            out.append(f"IRCTC allows at most {MAX_TATKAL_PASSENGERS} passengers per Tatkal booking; "
                       f"{len(selected)} are selected.")
        return out

    def fingerprint(self) -> str:
        """Changes whenever the selected passengers' details change; used to
        invalidate the manual "passengers reviewed" confirmation."""
        data = "|".join(
            f"{p.id}:{p.name}:{p.age}:{p.gender.value}:{p.berth_preference.value}:{p.food_preference.value}"
            for p in self.booking_passengers()
        )
        return hashlib.sha256(data.encode("utf-8")).hexdigest()[:16]
