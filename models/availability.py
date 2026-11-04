"""Availability domain model.

The key design rule: a result is only *verified* if the source explicitly
confirmed it, it is not a cached answer, it is not stale, and its status is
known. The UI and notifications must never present anything else as confirmed
availability.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import Enum

from models.journey import TatkalCategory, TravelClass


class AvailabilityStatus(str, Enum):
    AVAILABLE = "AVAILABLE"
    RAC = "RAC"
    WAITLIST = "WAITLIST"
    REGRET = "REGRET"
    NOT_AVAILABLE = "NOT_AVAILABLE"
    UNKNOWN = "UNKNOWN"


class Freshness(str, Enum):
    VERIFIED = "VERIFIED"
    CACHED = "CACHED"
    STALE = "STALE"
    UNCONFIRMED = "UNCONFIRMED"


@dataclass(frozen=True)
class AvailabilityQuery:
    train_number: str
    journey_date: date
    travel_class: TravelClass
    category: TatkalCategory
    from_station: str = ""
    to_station: str = ""

    @property
    def key(self) -> str:
        return "|".join(
            [self.train_number, self.journey_date.isoformat(), self.travel_class.value,
             self.category.value, self.from_station, self.to_station]
        )


@dataclass(frozen=True)
class AvailabilityResult:
    query: AvailabilityQuery
    status: AvailabilityStatus
    seats: int | None
    source: str
    fetched_at: datetime  # when we received it (aware UTC)
    source_timestamp: datetime | None = None  # when the source says the data was valid
    is_cached: bool = False
    confirmed_by_source: bool = False
    simulated: bool = False
    raw_text: str = ""
    id: int | None = field(default=None, compare=False)

    def age(self, now: datetime) -> timedelta:
        reference = self.source_timestamp or self.fetched_at
        return max(timedelta(0), now - reference)

    def is_stale(self, now: datetime, stale_after_seconds: float) -> bool:
        return self.age(now).total_seconds() > stale_after_seconds

    def freshness(self, now: datetime, stale_after_seconds: float) -> Freshness:
        if self.is_stale(now, stale_after_seconds):
            return Freshness.STALE
        if self.is_cached:
            return Freshness.CACHED
        if not self.confirmed_by_source or self.status is AvailabilityStatus.UNKNOWN:
            return Freshness.UNCONFIRMED
        return Freshness.VERIFIED

    def is_verified(self, now: datetime, stale_after_seconds: float) -> bool:
        return self.freshness(now, stale_after_seconds) is Freshness.VERIFIED

    @property
    def signature(self) -> tuple[str, int | None]:
        return (self.status.value, self.seats)

    def describe(self) -> str:
        if self.raw_text:
            return self.raw_text
        if self.status is AvailabilityStatus.AVAILABLE:
            return f"AVAILABLE {self.seats}" if self.seats is not None else "AVAILABLE"
        if self.status in (AvailabilityStatus.RAC, AvailabilityStatus.WAITLIST) and self.seats is not None:
            prefix = "RAC" if self.status is AvailabilityStatus.RAC else "WL"
            return f"{prefix} {self.seats}"
        return self.status.value.replace("_", " ")


class MonitorState(str, Enum):
    IDLE = "IDLE"
    RUNNING = "RUNNING"
    BACKING_OFF = "BACKING_OFF"
    STOPPED = "STOPPED"


@dataclass
class MonitorSnapshot:
    state: MonitorState = MonitorState.IDLE
    query: AvailabilityQuery | None = None
    last_result: AvailabilityResult | None = None
    last_success_at: datetime | None = None
    last_attempt_at: datetime | None = None
    next_attempt_at: datetime | None = None
    consecutive_failures: int = 0
    last_error: str | None = None
    requests_made: int = 0
