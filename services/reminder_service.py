"""Tatkal opening reminders.

Runs a 1-second local tick (no network). Fires TATKAL_REMINDER at each
configured "minutes before opening" threshold and TATKAL_OPENED at opening.
A reminder is only fired inside its one-minute window, so restarting the app
after a threshold has passed does not produce late, misleading alerts.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from core.clock import Clock
from core.config import NotificationConfig
from core.event_bus import EventBus, EventType
from core.scheduler import TatkalScheduler

OPENED_GRACE_SECONDS = 120


class ReminderService:
    def __init__(self, scheduler: TatkalScheduler, bus: EventBus, clock: Clock,
                 config: Callable[[], NotificationConfig]):
        self._scheduler = scheduler
        self._bus = bus
        self._clock = clock
        self._config = config
        self._fired: set[str] = set()

    def tick(self) -> None:
        jump = self._scheduler.check_clock()
        if jump is not None:
            self._bus.publish(EventType.CLOCK_CHANGED, delta_seconds=jump.delta_seconds)
        schedule = self._scheduler.schedule
        cfg = self._config()
        if schedule is None or not cfg.opening_reminders_enabled:
            return
        remaining = (schedule.opens_at_utc - self._clock.now_utc()).total_seconds()
        stamp = schedule.opens_at.isoformat()
        for minutes in cfg.reminder_minutes:
            key = f"{stamp}:{minutes}"
            if minutes * 60 - 60 < remaining <= minutes * 60 and key not in self._fired:
                self._fired.add(key)
                self._bus.publish(EventType.TATKAL_REMINDER, minutes=minutes, schedule=schedule)
        key = f"{stamp}:open"
        if -OPENED_GRACE_SECONDS <= remaining <= 0 and key not in self._fired:
            self._fired.add(key)
            self._bus.publish(EventType.TATKAL_OPENED, schedule=schedule)

    async def run_loop(self) -> None:
        while True:
            self.tick()
            await asyncio.sleep(1.0)
