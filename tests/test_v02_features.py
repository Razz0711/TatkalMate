import struct
from datetime import date, datetime, timezone

import pytest

from core.clock import FakeClock
from core.config import BrowserConfig, NotificationConfig, TimeSyncConfig
from core.event_bus import EventBus, EventType
from core.scheduler import IST, TatkalScheduler
from core.time_sync import NTP_EPOCH_DELTA, NtpError, OffsetClock, best_sample, sntp_query
from models.journey import Journey, TravelClass
from services.reminder_service import ReminderService
from services.time_sync_service import TimeSyncService
from tests.helpers import Recorder

T0 = 1_800_000_000.0  # local wall time of the request


def ntp_reply(server_time: float, li=0, mode=4, stratum=2, processing=0.001) -> bytes:
    def ts(t):
        sec = int(t) + NTP_EPOCH_DELTA
        return sec, int((t - int(t)) * 2**32)

    words = [0] * 12
    words[8], words[9] = ts(server_time)
    words[10], words[11] = ts(server_time + processing)
    data = bytearray(struct.pack("!12I", *words))
    data[0] = (li << 6) | (4 << 3) | mode
    data[1] = stratum
    return bytes(data)


def fake_wall(times):
    it = iter(times)
    return lambda: next(it)


def test_sntp_offset_and_delay():
    # Local clock is 2.5 s behind; 40 ms round trip.
    reply = ntp_reply(T0 + 0.02 + 2.5)
    s = sntp_query("x", transport=lambda h, p, t: reply, wall=fake_wall([T0, T0 + 0.041]))
    assert s.offset == pytest.approx(2.5, abs=1e-3)
    assert s.delay == pytest.approx(0.040, abs=1e-3)


@pytest.mark.parametrize("kw", [dict(li=3), dict(mode=3), dict(stratum=0)])
def test_sntp_rejects_unsynchronised(kw):
    with pytest.raises(NtpError):
        sntp_query("x", transport=lambda h, p, t: ntp_reply(T0, **kw), wall=fake_wall([T0, T0]))


def test_sntp_network_error_and_best_sample():
    def transport(host, p, t):
        if host == "down":
            raise OSError("timeout")
        return ntp_reply(T0 + (0.5 if host == "slow" else 0.01) / 2 + 1.0)

    walls = fake_wall([T0, T0, T0 + 0.5, T0, T0 + 0.01])  # failed query reads the clock once
    sample, errors = best_sample(["down", "slow", "fast"], transport, walls)
    assert len(errors) == 1 and sample.server == "fast"
    assert sample.offset == pytest.approx(1.0, abs=0.01)


def test_offset_clock_and_sync_service_do_not_trigger_clock_jump():
    base = FakeClock(datetime(2026, 11, 19, 9, 0, tzinfo=IST))
    clock = OffsetClock(base)
    bus = EventBus()
    sched = TatkalScheduler(clock)
    sched.set_journey(Journey("12951", "ST", "NDLS", date(2026, 11, 20), TravelClass.AC_3_TIER))
    before = sched.countdown().remaining.total_seconds()
    svc = TimeSyncService(clock, sched, bus, lambda: TimeSyncConfig())
    from core.time_sync import NtpSample
    st = svc.apply(NtpSample("ntp", 5.0, 0.03, base.now_utc()), [])
    assert st.synced and clock.offset == 5.0
    assert sched.countdown().remaining.total_seconds() == pytest.approx(before - 5.0)
    assert sched.check_clock() is None  # deliberate correction is not a "clock change"
    failed = svc.apply(None, ["a: timeout"])
    assert failed.error and clock.offset == 5.0  # keeps last good offset


def test_pre_opening_routine_events_in_order():
    clock = FakeClock(datetime(2026, 11, 19, 9, 40, tzinfo=IST))
    bus = EventBus()
    sched = TatkalScheduler(clock)
    sched.set_journey(Journey("12951", "ST", "NDLS", date(2026, 11, 20), TravelClass.AC_3_TIER))
    rem = ReminderService(sched, bus, clock, lambda: NotificationConfig(reminder_minutes=[10, 5, 1],
                                                                       refresh_reminder_minutes=2),
                          lambda: BrowserConfig(auto_launch_minutes_before=15))
    rec = Recorder(bus)
    for _ in range(21 * 60):
        rem.tick()
        clock.advance(1)
    seq = [(e.type, e.payload.get("minutes")) for e in rec.events]
    assert seq == [(EventType.BROWSER_AUTO_LAUNCH_DUE, 15), (EventType.TATKAL_REMINDER, 10),
                   (EventType.TATKAL_REMINDER, 5), (EventType.TATKAL_REFRESH_DUE, 2),
                   (EventType.TATKAL_REMINDER, 1), (EventType.TATKAL_OPENED, None)]


def test_auto_launch_handler_launches_brave_and_notifies(tmp_path):
    from core.paths import AppPaths
    from services.brave_service import BraveService
    from services.container import build_context

    launched = []
    brave = BraveService(EventBus(), platform="win32", env={"PROGRAMFILES": r"C:\PF"},
                         file_exists=lambda p: p.endswith("brave.exe"), registry_reader=lambda: [],
                         process_lister=lambda: [], launcher=lambda a, **k: launched.append(a))
    ctx = build_context(AppPaths(tmp_path).ensure(), brave=brave)
    try:
        ctx.brave.poll()
        ctx.bus.publish(EventType.BROWSER_AUTO_LAUNCH_DUE, minutes=15, schedule=None)
        assert launched and launched[0][1].startswith("https://www.irctc.co.in")
        assert ctx.notifications.recent()[0].title == "Brave opened on IRCTC"
    finally:
        ctx.shutdown()


def test_quick_panel_copies_fields(qtbot, tmp_path):
    from PySide6.QtGui import QGuiApplication

    from app.views.quick_panel import QuickPanel, journey_fields
    from core.paths import AppPaths
    from models.passenger import Gender, Passenger
    from services.brave_service import BraveService
    from services.container import build_context

    ctx = build_context(AppPaths(tmp_path).ensure(),
                        brave=BraveService(EventBus(), platform="linux", which=lambda c: None, process_lister=lambda: []))
    try:
        ctx.journeys.save(Journey("12951", "ST", "NDLS", date(2030, 1, 5), TravelClass.SLEEPER))
        ctx.passengers.add(Passenger("Raj Kumar", 22, Gender.MALE))
        assert ("Date", "05/01/2030") in journey_fields(ctx)
        panel = QuickPanel(ctx)
        qtbot.addWidget(panel)
        assert panel.windowFlags() & panel.windowFlags().WindowStaysOnTopHint
        panel.copy("12951", "train")
        assert QGuiApplication.clipboard().text() == "12951"
        assert panel.pax_grid.count() == 3
    finally:
        ctx.shutdown()
