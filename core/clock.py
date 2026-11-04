"""Clock abstraction.

All time-dependent logic receives a :class:`Clock` so it can be tested with a
:class:`FakeClock`, and so wall-clock changes (NTP sync, the user changing the
system time) can be detected by comparing wall time against monotonic time.
"""

from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Protocol


class Clock(Protocol):
    def now_utc(self) -> datetime: ...

    def monotonic(self) -> float: ...


class SystemClock:
    def now_utc(self) -> datetime:
        return datetime.now(timezone.utc)

    def monotonic(self) -> float:
        return time.monotonic()


class FakeClock:
    """Deterministic clock for tests. ``advance`` moves both wall and monotonic
    time; ``jump_wall`` moves only wall time, simulating a system clock change."""

    def __init__(self, start: datetime):
        if start.tzinfo is None:
            raise ValueError("FakeClock needs an aware datetime")
        self._wall = start.astimezone(timezone.utc)
        self._mono = 1000.0
        self._lock = threading.Lock()

    def now_utc(self) -> datetime:
        with self._lock:
            return self._wall

    def monotonic(self) -> float:
        with self._lock:
            return self._mono

    def advance(self, seconds: float) -> None:
        with self._lock:
            self._wall += timedelta(seconds=seconds)
            self._mono += seconds

    def jump_wall(self, seconds: float) -> None:
        with self._lock:
            self._wall += timedelta(seconds=seconds)

    def set_wall(self, when: datetime) -> None:
        with self._lock:
            self._wall = when.astimezone(timezone.utc)
