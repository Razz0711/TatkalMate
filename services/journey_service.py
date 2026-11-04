from __future__ import annotations

import logging

from core.event_bus import EventBus, EventType
from core.scheduler import TatkalScheduler
from database.repositories.journey_repository import JourneyRepository
from models.availability import AvailabilityQuery
from models.journey import Journey

log = logging.getLogger(__name__)


class JourneyService:
    def __init__(self, repo: JourneyRepository, scheduler: TatkalScheduler, bus: EventBus):
        self._repo = repo
        self._scheduler = scheduler
        self._bus = bus

    def load(self) -> Journey | None:
        journey = self._repo.get_active()
        self._scheduler.set_journey(journey)
        return journey

    def active(self) -> Journey | None:
        return self._scheduler.journey

    def save(self, journey: Journey) -> Journey:
        validated = journey.validate()
        saved = self._repo.save_active(validated)
        self._scheduler.set_journey(saved)
        log.info("journey saved", extra={"train": saved.train_number, "class": saved.travel_class.value})
        self._bus.publish(EventType.JOURNEY_CHANGED, journey=saved)
        return saved

    def clear(self) -> None:
        self._repo.clear_active()
        self._scheduler.set_journey(None)
        self._bus.publish(EventType.JOURNEY_CHANGED, journey=None)

    def availability_query(self) -> AvailabilityQuery | None:
        j = self.active()
        if not j:
            return None
        return AvailabilityQuery(
            train_number=j.train_number, journey_date=j.journey_date, travel_class=j.travel_class,
            category=j.category, from_station=j.from_station, to_station=j.to_station,
        )
