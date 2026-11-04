"""Tatkal opening-time scheduler.

Pure, UI-independent logic. Nothing in this module performs I/O.

Rules implemented (Indian Railways, current as of 2026 - re-check if IRCTC
changes them):

* Tatkal and Premium Tatkal booking opens one day in advance of the journey,
  excluding the journey date, counted from the date the train departs its
  *originating* station.
* AC classes (2A, 3A, 3E, CC, EC) open at 10:00 IST.
* Non-AC classes (SL, 2S, FC) open at 11:00 IST.

All computations are made in Asia/Kolkata. IST has no daylight saving, so the
fixed +05:30 offset is used as a fallback if the tz database is unavailable
(Windows without the ``tzdata`` package).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from enum import Enum

from core.clock import Clock
from models.journey import Journey, TravelClass

try:  # pragma: no cover - exercised implicitly
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
except Exception:  # pragma: no cover - only without tzdata
    IST = timezone(timedelta(hours=5, minutes=30), "IST")

AC_OPENING_TIME = time(10, 0)
NON_AC_OPENING_TIME = time(11, 0)
ADVANCE_DAYS = 1
CLOCK_JUMP_THRESHOLD_SECONDS = 2.0


class TatkalWindow(str, Enum):
    AC = "AC"
    NON_AC = "NON_AC"

    @property
    def label(self) -> str:
        return "AC Tatkal" if self is TatkalWindow.AC else "Non-AC Tatkal"

    @property
    def opening_time(self) -> time:
        return AC_OPENING_TIME if self is TatkalWindow.AC else NON_AC_OPENING_TIME


class Phase(str, Enum):
    NO_JOURNEY = "NO_JOURNEY"
    BEFORE_BOOKING_DAY = "BEFORE_BOOKING_DAY"
    BOOKING_DAY_PRE_OPEN = "BOOKING_DAY_PRE_OPEN"
    OPEN = "OPEN"
    JOURNEY_PASSED = "JOURNEY_PASSED"


@dataclass(frozen=True)
class TatkalSchedule:
    window: TatkalWindow
    origin_departure_date: date
    booking_date: date
    opens_at: datetime  # aware, Asia/Kolkata

    @property
    def opens_at_utc(self) -> datetime:
        return self.opens_at.astimezone(timezone.utc)


@dataclass(frozen=True)
class Countdown:
    remaining: timedelta  # never negative
    is_open: bool

    @property
    def total_milliseconds(self) -> int:
        return int(self.remaining.total_seconds() * 1000)

    @property
    def parts(self) -> tuple[int, int, int, int]:
        ms = self.total_milliseconds
        hours, ms = divmod(ms, 3_600_000)
        minutes, ms = divmod(ms, 60_000)
        seconds, ms = divmod(ms, 1000)
        return hours, minutes, seconds, ms

    def format(self) -> str:
        h, m, s, ms = self.parts
        return f"{h:02d}:{m:02d}:{s:02d}.{ms:03d}"


@dataclass(frozen=True)
class ClockJump:
    delta_seconds: float  # positive: wall clock moved forward unexpectedly
    detected_at: datetime


def window_for(travel_class: TravelClass) -> TatkalWindow:
    return TatkalWindow.AC if travel_class.is_ac else TatkalWindow.NON_AC


def compute_schedule(
    travel_class: TravelClass, journey_date: date, boarding_day_offset: int = 0
) -> TatkalSchedule:
    if boarding_day_offset < 0:
        raise ValueError("boarding_day_offset cannot be negative")
    window = window_for(travel_class)
    origin_date = journey_date - timedelta(days=boarding_day_offset)
    booking_date = origin_date - timedelta(days=ADVANCE_DAYS)
    opens_at = datetime.combine(booking_date, window.opening_time, tzinfo=IST)
    return TatkalSchedule(window, origin_date, booking_date, opens_at)


def schedule_for(journey: Journey) -> TatkalSchedule:
    return compute_schedule(
        journey.travel_class, journey.journey_date, journey.boarding_day_offset
    )


def to_ist(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("naive datetimes are not accepted; pass an aware datetime")
    return moment.astimezone(IST)


class TatkalScheduler:
    """Holds the active journey and answers time questions about it.

    The countdown is recomputed from the wall clock on every call, so it is
    correct after an application restart and after the system clock changes.
    ``check_clock`` detects such changes by comparing wall-clock progress with
    monotonic progress since the previous call.
    """

    def __init__(self, clock: Clock):
        self._clock = clock
        self._journey: Journey | None = None
        self._schedule: TatkalSchedule | None = None
        self._last_wall = clock.now_utc()
        self._last_mono = clock.monotonic()

    @property
    def journey(self) -> Journey | None:
        return self._journey

    @property
    def schedule(self) -> TatkalSchedule | None:
        return self._schedule

    def set_journey(self, journey: Journey | None) -> None:
        self._journey = journey
        self._schedule = schedule_for(journey) if journey else None

    def now_ist(self) -> datetime:
        return to_ist(self._clock.now_utc())

    def countdown(self) -> Countdown | None:
        if not self._schedule:
            return None
        delta = self._schedule.opens_at_utc - self._clock.now_utc()
        if delta <= timedelta(0):
            return Countdown(timedelta(0), True)
        return Countdown(delta, False)

    def phase(self) -> Phase:
        if not self._schedule or not self._journey:
            return Phase.NO_JOURNEY
        now = self.now_ist()
        if now.date() > self._journey.journey_date:
            return Phase.JOURNEY_PASSED
        if now >= self._schedule.opens_at:
            return Phase.OPEN
        if now.date() == self._schedule.booking_date:
            return Phase.BOOKING_DAY_PRE_OPEN
        return Phase.BEFORE_BOOKING_DAY

    def reset_clock_baseline(self) -> None:
        self._last_wall = self._clock.now_utc()
        self._last_mono = self._clock.monotonic()

    def check_clock(self) -> ClockJump | None:
        wall = self._clock.now_utc()
        mono = self._clock.monotonic()
        wall_elapsed = (wall - self._last_wall).total_seconds()
        mono_elapsed = mono - self._last_mono
        self._last_wall, self._last_mono = wall, mono
        drift = wall_elapsed - mono_elapsed
        if abs(drift) >= CLOCK_JUMP_THRESHOLD_SECONDS:
            return ClockJump(drift, wall)
        return None
