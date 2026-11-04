import asyncio
from datetime import date, timedelta

import pytest

import services.availability_service as avs
from core.clock import SystemClock
from core.config import NotificationConfig
from core.event_bus import EventBus, EventType
from core.exceptions import (
    ProviderError,
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from database.repositories.availability_repository import AvailabilityRepository
from database.repositories.notification_repository import NotificationRepository
from models.availability import AvailabilityQuery, AvailabilityStatus as S, Freshness, MonitorState
from models.journey import TatkalCategory, TravelClass
from models.notification import Severity
from providers.base_provider import ProviderInfo
from providers.mock_provider import MockAvailabilityProvider
from services.alert_service import AlertService
from services.notification_service import NotificationService
from tests.helpers import Recorder, fake_clock, fast_monitoring, memory_db

Q = AvailabilityQuery("12951", date(2026, 11, 20), TravelClass.AC_3_TIER, TatkalCategory.TATKAL, "ST", "NDLS")


def make(script, clock=None, cfg=None, db=None, latency=(0, 0)):
    clock = clock or fake_clock()
    db = db or memory_db()
    bus = EventBus()
    provider = MockAvailabilityProvider(clock=clock, script=script, latency=latency)
    svc = avs.AvailabilityService(provider, AvailabilityRepository(db), bus, clock, cfg or fast_monitoring())
    return svc, bus, clock, provider, db


def run(coro):
    return asyncio.run(coro)


def test_check_records_result_and_history():
    svc, bus, clock, provider, _ = make([(S.AVAILABLE, 12)])
    rec = Recorder(bus)
    r = run(svc.check_now(Q))
    assert r.status is S.AVAILABLE and r.id is not None and r.simulated
    assert len(svc.history(Q)) == 1
    assert rec.of(EventType.AVAILABILITY_RESULT)[0].payload["freshness"] is Freshness.VERIFIED
    assert not rec.of(EventType.AVAILABILITY_CHANGED)  # first observation is a baseline
    assert svc.snapshot().last_success_at == clock.now_utc()


def test_rate_limit_floor_applies_to_manual_checks():
    svc, _, clock, provider, _ = make([(S.WAITLIST, 5)])
    run(svc.check_now(Q))
    with pytest.raises(ProviderRateLimitedError) as e:
        run(svc.check_now(Q))
    assert e.value.retry_after == pytest.approx(60)
    assert provider.calls == 1
    clock.advance(59)
    with pytest.raises(ProviderRateLimitedError):
        run(svc.check_now(Q))
    clock.advance(1)
    run(svc.check_now(Q))
    assert provider.calls == 2


def test_change_detection_publishes_previous_and_new():
    svc, bus, clock, _, _ = make([(S.WAITLIST, 5), (S.AVAILABLE, 3)])
    rec = Recorder(bus)
    run(svc.check_now(Q))
    clock.advance(60)
    run(svc.check_now(Q))
    changed = rec.of(EventType.AVAILABILITY_CHANGED)
    assert len(changed) == 1
    assert changed[0].payload["previous"] == ("WAITLIST", 5)
    assert changed[0].payload["result"].signature == ("AVAILABLE", 3)
    assert [c for _, c in svc.history(Q)] == [True, True]  # newest first: change, baseline


def test_restart_does_not_raise_spurious_change():
    db = memory_db()
    svc, _, clock, _, _ = make([(S.RAC, 4)], db=db)
    run(svc.check_now(Q))
    svc2, bus2, _, _, _ = make([(S.RAC, 4)], clock=clock, db=db)
    rec = Recorder(bus2)
    run(svc2.check_now(Q))
    assert not rec.of(EventType.AVAILABILITY_CHANGED)


def test_concurrent_checks_share_one_request():
    svc, _, _, provider, _ = make([(S.AVAILABLE, 9)], latency=(0.05, 0.05))

    async def both():
        return await asyncio.gather(svc.check_now(Q), svc.check_now(Q))

    a, b = run(both())
    assert provider.calls == 1
    assert a.id == b.id


def test_timeout_is_reported_as_failure():
    class Slow(MockAvailabilityProvider):
        async def fetch_availability(self, query):
            await asyncio.sleep(5)

    clock = fake_clock()
    svc = avs.AvailabilityService(Slow(clock=clock), AvailabilityRepository(memory_db()), EventBus(), clock,
                                  fast_monitoring(request_timeout_seconds=0.05))
    with pytest.raises(ProviderTimeoutError):
        run(svc.check_now(Q))
    assert svc.snapshot().consecutive_failures == 1


def test_unexpected_provider_bug_never_looks_like_data():
    svc, _, _, _, _ = make([ZeroDivisionError("boom")])
    with pytest.raises(ProviderError):
        run(svc.check_now(Q))
    assert svc.history(Q) == []


@pytest.mark.parametrize("extra,expected", [
    ({"is_cached": True, "confirmed_by_source": False}, Freshness.CACHED),
    ({"confirmed_by_source": False}, Freshness.UNCONFIRMED),
])
def test_unverified_results_are_labelled(extra, expected):
    svc, bus, _, _, _ = make([(S.AVAILABLE, 2, extra)])
    rec = Recorder(bus)
    run(svc.check_now(Q))
    assert rec.of(EventType.AVAILABILITY_RESULT)[0].payload["freshness"] is expected


def test_stale_source_timestamp_is_detected():
    clock = fake_clock()
    old = clock.now_utc() - timedelta(hours=1)
    svc, bus, _, _, _ = make([(S.AVAILABLE, 2, {"source_timestamp": old})], clock=clock)
    rec = Recorder(bus)
    run(svc.check_now(Q))
    assert rec.of(EventType.AVAILABILITY_RESULT)[0].payload["freshness"] is Freshness.STALE


def _alerts(bus, clock, db):
    notes = NotificationService(NotificationRepository(db), bus, clock, lambda: NotificationConfig())
    AlertService(bus, notes)
    return notes


def test_alerts_only_claim_seats_for_verified_results():
    svc, bus, clock, _, db = make([(S.WAITLIST, 8), (S.AVAILABLE, 4, {"is_cached": True}), (S.AVAILABLE, 6)])
    notes = _alerts(bus, clock, db)
    run(svc.check_now(Q)); clock.advance(60)
    run(svc.check_now(Q))
    titles = [n.title for n in notes.recent()]
    assert any("Unverified change" in t for t in titles)
    assert not any("Seats available" in t for t in titles)
    clock.advance(60)
    run(svc.check_now(Q))
    top = notes.recent()[0]
    assert "Seats available" in top.title and top.severity is Severity.SUCCESS
    assert top.title.startswith("[Simulated]")


def test_backoff_grows_and_caps():
    svc, *_ = make([(S.AVAILABLE, 1)], cfg=fast_monitoring(backoff_base_seconds=30, backoff_max_seconds=600))
    delays = [svc.backoff_delay(n) for n in range(1, 10)]
    assert delays[0] >= 60  # never below the request floor
    assert delays[3] > delays[2]
    assert max(delays) <= 600 * 1.15


@pytest.fixture
def fast_floor(monkeypatch):
    monkeypatch.setattr(avs, "HARD_MIN_POLL_SECONDS", 0.01)
    MockAvailabilityProvider.info = ProviderInfo("mock", "Mock", "", False, 0.01)
    yield
    MockAvailabilityProvider.info = ProviderInfo("mock", "Mock provider (simulated data)",
                                                 "Generates simulated availability locally. Not connected to any railway data source.",
                                                 False, 60.0)


def test_monitor_loop_backs_off_alerts_and_recovers(fast_floor):
    script = [ProviderUnavailableError("down"), ProviderTimeoutError("slow"), (S.WAITLIST, 3), (S.AVAILABLE, 1)]
    svc, bus, _, provider, _ = make(script, clock=SystemClock())
    rec = Recorder(bus)

    async def go():
        task = asyncio.create_task(svc.run_monitor(Q))
        for _ in range(200):
            await asyncio.sleep(0.02)
            if rec.of(EventType.AVAILABILITY_CHANGED):
                break
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    run(go())
    assert len(rec.of(EventType.MONITOR_FAILURE)) == 1
    assert len(rec.of(EventType.MONITOR_RECOVERED)) == 1
    assert rec.of(EventType.AVAILABILITY_CHANGED)
    assert svc.snapshot().state is MonitorState.STOPPED
    states = [e.payload["snapshot"].state for e in rec.of(EventType.MONITOR_STATE_CHANGED)]
    assert MonitorState.BACKING_OFF in states


def test_monitor_stops_when_provider_not_configured(fast_floor):
    svc, bus, _, _, _ = make([ProviderNotConfiguredError("no key")], clock=SystemClock())
    rec = Recorder(bus)
    run(asyncio.wait_for(svc.run_monitor(Q), 2))
    fail = rec.of(EventType.MONITOR_FAILURE)
    assert fail and fail[0].payload["fatal"]
    assert svc.snapshot().state is MonitorState.STOPPED
