"""Periodically measures the PC clock offset against NTP and corrects the app clock."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from core.config import TimeSyncConfig
from core.event_bus import EventBus, EventType
from core.scheduler import TatkalScheduler
from core.time_sync import NtpSample, OffsetClock, Transport, best_sample, udp_transport

log = logging.getLogger(__name__)

WARN_OFFSET_SECONDS = 1.0


@dataclass(frozen=True)
class TimeSyncStatus:
    synced: bool
    offset: float
    server: str | None
    delay: float | None
    last_attempt: datetime | None
    last_success: datetime | None
    error: str | None


class TimeSyncService:
    def __init__(self, clock: OffsetClock, scheduler: TatkalScheduler, bus: EventBus,
                 config: Callable[[], TimeSyncConfig], transport: Transport = udp_transport):
        self._clock = clock
        self._scheduler = scheduler
        self._bus = bus
        self._config = config
        self._transport = transport
        self._status = TimeSyncStatus(False, 0.0, None, None, None, None, None)

    @property
    def status(self) -> TimeSyncStatus:
        return self._status

    def apply(self, sample: NtpSample | None, errors: list[str]) -> TimeSyncStatus:
        now = self._clock.raw_now_utc()
        if sample is None:
            self._status = TimeSyncStatus(self._status.synced, self._clock.offset, self._status.server,
                                          self._status.delay, now, self._status.last_success,
                                          "; ".join(errors) or "no NTP server answered")
            log.warning("ntp sync failed", extra={"servers_failed": len(errors)})
        else:
            self._clock.set_offset(sample.offset)
            # The offset change is deliberate: don't report it as a system clock jump.
            self._scheduler.reset_clock_baseline()
            self._status = TimeSyncStatus(True, sample.offset, sample.server, sample.delay, now, now, None)
            log.info("ntp synced", extra={"offset_ms": round(sample.offset * 1000, 1), "server": sample.server})
        self._bus.publish(EventType.TIME_SYNCED, status=self._status)
        return self._status

    async def sync_once(self) -> TimeSyncStatus:
        cfg = self._config()
        sample, errors = await asyncio.to_thread(best_sample, list(cfg.servers), self._transport)
        return self.apply(sample, errors)

    async def run_loop(self) -> None:
        while True:
            cfg = self._config()
            if cfg.enabled:
                try:
                    await self.sync_once()
                except Exception:
                    log.exception("time sync crashed")
            else:
                self._clock.set_offset(0.0)
                self._scheduler.reset_clock_baseline()
            await asyncio.sleep(cfg.interval_seconds if self._status.synced else min(120.0, cfg.interval_seconds))
