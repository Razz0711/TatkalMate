"""Simulated availability provider for development and testing.

Every result is flagged ``simulated=True`` and the UI labels it as simulated
data. It does not contact any server.
"""

from __future__ import annotations

import asyncio
import random
from datetime import timedelta

from core.clock import Clock, SystemClock
from core.exceptions import ProviderTimeoutError, ProviderUnavailableError
from models.availability import AvailabilityQuery, AvailabilityResult, AvailabilityStatus
from providers.base_provider import AvailabilityProvider, ProviderHealth, ProviderInfo, ProviderStatus


class MockAvailabilityProvider(AvailabilityProvider):
    info = ProviderInfo(
        id="mock",
        name="Mock provider (simulated data)",
        description="Generates simulated availability locally. Not connected to any railway data source.",
        is_real_source=False,
        min_request_interval_seconds=60.0,
    )

    def __init__(
        self,
        clock: Clock | None = None,
        seed: int | None = None,
        failure_rate: float = 0.05,
        timeout_rate: float = 0.02,
        cached_rate: float = 0.1,
        latency: tuple[float, float] = (0.05, 0.4),
        script: list[object] | None = None,
    ):
        """``script`` (tests) is a list of ``(status, seats)`` tuples,
        ``(status, seats, {"is_cached": True, ...})`` tuples or exception
        instances returned in order; when it runs out, the last item repeats."""
        self._clock = clock or SystemClock()
        self._rng = random.Random(seed)
        self.failure_rate = failure_rate
        self.timeout_rate = timeout_rate
        self.cached_rate = cached_rate
        self.latency = latency
        self._script = list(script) if script else None
        self._script_index = 0
        self._seats: dict[str, int] = {}
        self.calls = 0

    async def fetch_availability(self, query: AvailabilityQuery) -> AvailabilityResult:
        self.calls += 1
        if self.latency[1] > 0:
            await asyncio.sleep(self._rng.uniform(*self.latency))
        now = self._clock.now_utc()
        if self._script is not None:
            item = self._script[min(self._script_index, len(self._script) - 1)]
            self._script_index += 1
            if isinstance(item, BaseException):
                raise item
            status, seats, *rest = item  # type: ignore[misc]
            extra = rest[0] if rest else {}
            return AvailabilityResult(
                query=query, status=status, seats=seats, source=self.info.name, fetched_at=now,
                source_timestamp=extra.get("source_timestamp", now), is_cached=extra.get("is_cached", False),
                confirmed_by_source=extra.get("confirmed_by_source", True), simulated=True,
            )
        roll = self._rng.random()
        if roll < self.timeout_rate:
            raise ProviderTimeoutError("simulated timeout")
        if roll < self.timeout_rate + self.failure_rate:
            raise ProviderUnavailableError("simulated network failure")
        status, seats = self._random_walk(query.key)
        cached = self._rng.random() < self.cached_rate
        source_ts = now - timedelta(seconds=self._rng.randint(300, 1200)) if cached else now
        return AvailabilityResult(
            query=query, status=status, seats=seats, source=self.info.name, fetched_at=now,
            source_timestamp=source_ts, is_cached=cached, confirmed_by_source=not cached, simulated=True,
        )

    def _random_walk(self, key: str) -> tuple[AvailabilityStatus, int | None]:
        level = self._seats.get(key, self._rng.randint(-20, 40))
        level += self._rng.randint(-6, 3)
        self._seats[key] = level
        if level > 0:
            return AvailabilityStatus.AVAILABLE, level
        if level > -8:
            return AvailabilityStatus.RAC, -level + 1
        if level > -60:
            return AvailabilityStatus.WAITLIST, -level - 7
        return AvailabilityStatus.REGRET, None

    async def check_status(self) -> ProviderStatus:
        return ProviderStatus(ProviderHealth.OK, "Simulated data only - no real source connected")
