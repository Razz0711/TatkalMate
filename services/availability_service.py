"""Availability monitoring engine.

Guarantees:

* **Rate limiting.** Consecutive provider requests are at least
  ``max(provider.min_request_interval_seconds, HARD_MIN_POLL_SECONDS)`` apart,
  for manual checks and the monitor loop alike. The monitor polls every
  ``max(poll_interval_seconds, that gap)``.
* **No duplicate requests.** Concurrent checks for the same query share a
  single in-flight request.
* **Timeouts and backoff.** Each request has a timeout; failures back off
  exponentially (with jitter) up to ``backoff_max_seconds``; a provider's
  ``Retry-After`` is honoured.
* **Honesty.** Results carry their freshness (verified, cached, stale,
  unconfirmed). Change events include it, and the alert layer only announces
  confirmed availability for VERIFIED results.
* **History.** Every result is stored with timestamps; changes are flagged.
* **Restart safety.** The last known signature is reloaded from the database,
  so a restart does not produce a spurious "changed" alert.
"""

from __future__ import annotations

import asyncio
import copy
import logging
import random
import threading
from datetime import timedelta
from typing import TYPE_CHECKING

from core.clock import Clock
from core.config import HARD_MIN_POLL_SECONDS, MonitoringConfig
from core.event_bus import EventBus, EventType
from core.exceptions import (
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
)
from database.repositories.availability_repository import AvailabilityRepository
from models.availability import AvailabilityQuery, AvailabilityResult, MonitorSnapshot, MonitorState
from providers.base_provider import AvailabilityProvider

if TYPE_CHECKING:
    from core.async_runtime import AsyncRuntime

log = logging.getLogger(__name__)

MONITOR_TASK = "availability-monitor"
STALE_CHECK_SECONDS = 5.0


class AvailabilityService:
    def __init__(
        self,
        provider: AvailabilityProvider,
        repo: AvailabilityRepository,
        bus: EventBus,
        clock: Clock,
        config: MonitoringConfig,
        runtime: "AsyncRuntime | None" = None,
        rng: random.Random | None = None,
    ):
        self._provider = provider
        self._repo = repo
        self._bus = bus
        self._clock = clock
        self.config = config
        self._runtime = runtime
        self._rng = rng or random.Random()
        self._lock = threading.Lock()
        self._snapshot = MonitorSnapshot()
        self._inflight: dict[str, asyncio.Future[AvailabilityResult]] = {}
        self._last_request_mono: float | None = None
        self._signatures: dict[str, tuple] = {}
        self._wake: asyncio.Event | None = None
        self._stale_flagged = False
        self._failure_alerted = False

    # ----- properties -------------------------------------------------
    @property
    def provider(self) -> AvailabilityProvider:
        return self._provider

    def set_provider(self, provider: AvailabilityProvider) -> None:
        if self.is_monitoring:
            raise RuntimeError("stop monitoring before switching provider")
        self._provider = provider
        self._last_request_mono = None

    @property
    def min_request_gap(self) -> float:
        return max(HARD_MIN_POLL_SECONDS, self._provider.info.min_request_interval_seconds)

    @property
    def effective_poll_interval(self) -> float:
        return max(self.config.poll_interval_seconds, self.min_request_gap)

    @property
    def is_monitoring(self) -> bool:
        with self._lock:
            return self._snapshot.state in (MonitorState.RUNNING, MonitorState.BACKING_OFF)

    def snapshot(self) -> MonitorSnapshot:
        with self._lock:
            return copy.copy(self._snapshot)

    def retry_after(self) -> float:
        if self._last_request_mono is None:
            return 0.0
        return max(0.0, self.min_request_gap - (self._clock.monotonic() - self._last_request_mono))

    def history(self, query: AvailabilityQuery, limit: int = 200, changes_only: bool = False):
        return self._repo.history(query, limit, changes_only)

    def latest(self, query: AvailabilityQuery) -> AvailabilityResult | None:
        with self._lock:
            last = self._snapshot.last_result
        if last and last.query.key == query.key:
            return last
        return self._repo.latest(query)

    # ----- control (thread-safe, need a runtime) ------------------------
    def start(self, query: AvailabilityQuery) -> None:
        if not self._runtime:
            raise RuntimeError("no runtime attached")
        self._runtime.spawn(MONITOR_TASK, lambda: self.run_monitor(query))

    def stop(self) -> None:
        if self._runtime:
            self._runtime.cancel(MONITOR_TASK)

    def nudge(self) -> None:
        """Ask a sleeping monitor to re-check early (e.g. network restored).
        Rate limits still apply."""
        if self._runtime:
            self._runtime.call(lambda: self._wake.set() if self._wake else None)

    # ----- requests ---------------------------------------------------
    async def check_now(self, query: AvailabilityQuery) -> AvailabilityResult:
        """One manual check. Joins an in-flight request for the same query;
        otherwise raises ProviderRateLimitedError if the gap has not elapsed."""
        existing = self._inflight.get(query.key)
        if existing is not None:
            return await asyncio.shield(existing)
        wait = self.retry_after()
        if wait > 0:
            raise ProviderRateLimitedError(f"next request allowed in {wait:.0f} s", wait)
        return await self._fetch(query)

    async def _fetch(self, query: AvailabilityQuery) -> AvailabilityResult:
        existing = self._inflight.get(query.key)
        if existing is not None:
            return await asyncio.shield(existing)
        loop = asyncio.get_running_loop()
        future: asyncio.Future[AvailabilityResult] = loop.create_future()
        future.add_done_callback(lambda f: f.cancelled() or f.exception())  # mark retrieved
        self._inflight[query.key] = future
        self._last_request_mono = self._clock.monotonic()  # reserve the slot before awaiting
        self._update(last_attempt_at=self._clock.now_utc(), requests_made=self._snapshot.requests_made + 1)
        try:
            try:
                result = await asyncio.wait_for(
                    self._provider.fetch_availability(query), timeout=self.config.request_timeout_seconds
                )
            except asyncio.TimeoutError:
                raise ProviderTimeoutError(
                    f"no answer within {self.config.request_timeout_seconds:.0f} s"
                ) from None
            except ProviderError:
                raise
            except Exception as exc:  # provider bug: never let it look like data
                log.exception("provider raised an unexpected error")
                raise ProviderError(f"unexpected provider error: {exc}") from exc
        except ProviderError as err:
            self._record_failure(err)
            future.set_exception(err)
            raise
        except asyncio.CancelledError:
            future.cancel()
            raise
        else:
            stored = self._record_success(result)
            future.set_result(stored)
            return stored
        finally:
            self._inflight.pop(query.key, None)

    def _record_success(self, result: AvailabilityResult) -> AvailabilityResult:
        key = result.query.key
        if key not in self._signatures:
            previous = self._repo.latest(result.query)
            if previous is not None:
                self._signatures[key] = previous.signature
        previous_sig = self._signatures.get(key)
        changed = previous_sig is not None and previous_sig != result.signature
        self._signatures[key] = result.signature
        stored = self._repo.add(result, changed=changed or previous_sig is None)

        now = self._clock.now_utc()
        freshness = stored.freshness(now, self.config.stale_after_seconds)
        recovered = self._failure_alerted
        self._failure_alerted = False
        self._stale_flagged = False
        self._update(last_result=stored, last_success_at=now, consecutive_failures=0, last_error=None)
        log.info(
            "availability result",
            extra={"status": stored.status.value, "freshness": freshness.value, "changed": changed,
                   "simulated": stored.simulated},
        )
        if recovered:
            self._bus.publish(EventType.MONITOR_RECOVERED, provider=self._provider.info.name)
        self._bus.publish(EventType.AVAILABILITY_RESULT, result=stored, freshness=freshness)
        if changed:
            self._bus.publish(
                EventType.AVAILABILITY_CHANGED, result=stored, previous=previous_sig, freshness=freshness
            )
        return stored

    def _record_failure(self, err: ProviderError) -> None:
        failures = self._snapshot.consecutive_failures + 1
        self._update(consecutive_failures=failures, last_error=str(err))
        log.warning("availability check failed", extra={"error": type(err).__name__, "failures": failures})
        fatal = isinstance(err, ProviderNotConfiguredError)
        if (failures >= self.config.failure_alert_threshold or fatal) and not self._failure_alerted:
            self._failure_alerted = True
            self._bus.publish(
                EventType.MONITOR_FAILURE, provider=self._provider.info.name, error=str(err),
                failures=failures, fatal=fatal,
            )

    # ----- monitor loop -----------------------------------------------
    def backoff_delay(self, failures: int) -> float:
        base = self.config.backoff_base_seconds * (2 ** max(0, failures - 1))
        delay = min(self.config.backoff_max_seconds, base) * self._rng.uniform(0.85, 1.15)
        return max(self.min_request_gap, delay)

    async def run_monitor(self, query: AvailabilityQuery) -> None:
        self._wake = asyncio.Event()
        self._failure_alerted = False
        self._update(state=MonitorState.RUNNING, query=query, last_error=None)
        self._publish_state()
        delay = 0.0
        try:
            while True:
                delay = max(delay, self.retry_after())
                if delay > 0:
                    self._update(next_attempt_at=self._clock.now_utc() + timedelta(seconds=delay))
                    self._publish_state()
                    woke = await self._wait(delay)
                    if woke and self.retry_after() > 0:
                        delay = self.retry_after()
                        continue
                self._update(next_attempt_at=None)
                try:
                    await self._fetch(query)
                    delay = self.effective_poll_interval
                    self._update(state=MonitorState.RUNNING)
                except ProviderNotConfiguredError as err:
                    self._update(state=MonitorState.STOPPED, last_error=str(err))
                    return
                except ProviderRateLimitedError as err:
                    delay = max(err.retry_after, self.effective_poll_interval)
                    self._update(state=MonitorState.BACKING_OFF)
                except ProviderError:
                    delay = self.backoff_delay(self._snapshot.consecutive_failures)
                    self._update(state=MonitorState.BACKING_OFF)
                self._publish_state()
        finally:
            self._wake = None
            self._update(state=MonitorState.STOPPED, next_attempt_at=None)
            self._publish_state()

    async def _wait(self, seconds: float) -> bool:
        """Sleep up to ``seconds``; returns True if woken early by nudge()."""
        deadline = self._clock.monotonic() + seconds
        while True:
            self._check_stale()
            remaining = deadline - self._clock.monotonic()
            if remaining <= 0:
                return False
            assert self._wake is not None
            try:
                await asyncio.wait_for(self._wake.wait(), timeout=min(remaining, STALE_CHECK_SECONDS))
                self._wake.clear()
                return True
            except asyncio.TimeoutError:
                continue

    def _check_stale(self) -> None:
        with self._lock:
            last = self._snapshot.last_result
        if last is None or self._stale_flagged:
            return
        if last.is_stale(self._clock.now_utc(), self.config.stale_after_seconds):
            self._stale_flagged = True
            self._bus.publish(EventType.AVAILABILITY_STALE, result=last)

    # ----- helpers ------------------------------------------------------
    def _update(self, **changes) -> None:
        with self._lock:
            for k, v in changes.items():
                setattr(self._snapshot, k, v)

    def _publish_state(self) -> None:
        self._bus.publish(EventType.MONITOR_STATE_CHANGED, snapshot=self.snapshot())
