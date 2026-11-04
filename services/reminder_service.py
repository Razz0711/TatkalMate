"""Pre-opening routine.

A 1-second local tick (no network) fires, for the active journey:

* ``BROWSER_AUTO_LAUNCH_DUE`` at ``browser.auto_launch_minutes_before``,
* ``TATKAL_REMINDER`` at each ``notifications.reminder_minutes`` (default T-10/5/1),
* ``TATKAL_REFRESH_DUE`` at ``notifications.refresh_reminder_minutes`` (default T-2),
* ``TATKAL_OPENED`` at opening.

Each fires once, and only inside its one-minute window, so a restart after a
threshold has passed never produces a late, misleading alert.
"""

from __future__ import annotations

import asyncio
from typing import Callable

from core.clock import Clock
from core.config import BrowserConfig, NotificationConfig
from core.event_bus import EventBus, EventType
from core.scheduler import TatkalScheduler

OPENED_GRACE_SECONDS = 120


class ReminderService:
    def __init__(self, scheduler: TatkalScheduler, bus: EventBus, clock: Clock,
                 config: Callable[[], NotificationConfig],
                 browser_config: Callable[[], BrowserConfig] = BrowserConfig):
        self._scheduler = scheduler
        self._bus = bus
        self._clock = clock
        self._config = config
        self._browser_config = browser_config
        self._fired: set[str] = set()

    def _due(self, key: str, remaining: float, minutes: int) -> bool:
        if minutes <= 0 or key in self._fired:
            return False
        if minutes * 60 - 60 < remaining <= minutes * 60:
            self._fired.add(key)
            return True
        return False

    def tick(self) -> None:
        jump = self._scheduler.check_clock()
        if jump is not None:
            self._bus.publish(EventType.CLOCK_CHANGED, delta_seconds=jump.delta_seconds)
        schedule = self._scheduler.schedule
        if schedule is None:
            return
        cfg = self._config()
        remaining = (schedule.opens_at_utc - self._clock.now_utc()).total_seconds()
        stamp = schedule.opens_at.isoformat()

        launch_at = self._browser_config().auto_launch_minutes_before
        if self._due(f"{stamp}:launch", remaining, launch_at):
            self._bus.publish(EventType.BROWSER_AUTO_LAUNCH_DUE, minutes=launch_at, schedule=schedule)

        if not cfg.opening_reminders_enabled:
            return
        for minutes in cfg.reminder_minutes:
            if self._due(f"{stamp}:{minutes}", remaining, minutes):
                self._bus.publish(EventType.TATKAL_REMINDER, minutes=minutes, schedule=schedule)
        if self._due(f"{stamp}:refresh", remaining, cfg.refresh_reminder_minutes):
            self._bus.publish(EventType.TATKAL_REFRESH_DUE, minutes=cfg.refresh_reminder_minutes, schedule=schedule)
        key = f"{stamp}:open"
        if -OPENED_GRACE_SECONDS <= remaining <= 0 and key not in self._fired:
            self._fired.add(key)
            self._bus.publish(EventType.TATKAL_OPENED, schedule=schedule)

    async def run_loop(self) -> None:
        while True:
            self.tick()
            await asyncio.sleep(1.0)
