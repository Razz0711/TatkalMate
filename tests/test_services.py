import json
import logging
from datetime import date, timedelta

import pytest

from core.config import AppConfig, ConfigManager, HARD_MIN_POLL_SECONDS, NotificationConfig, load_dotenv
from core.event_bus import EventBus, EventType
from core.logging_config import JsonFormatter, RedactionFilter, Redactor
from core.recovery import SessionGuard, write_crash_report
from core.scheduler import TatkalScheduler
from core.secure_codec import SecureCodec
from database.repositories.journey_repository import JourneyRepository
from database.repositories.notification_repository import NotificationRepository
from database.repositories.passenger_repository import PassengerRepository
from database.repositories.state_repository import StateRepository
from models.journey import Journey, TravelClass
from models.notification import NotificationKind, Severity
from models.passenger import Gender, Passenger
from models.readiness import CheckSource, CheckState
from services.alert_service import AlertService
from services.brave_service import BraveService, BrowserState
from services.journey_service import JourneyService
from services.notification_service import NotificationService
from services.passenger_service import PassengerService
from services.readiness_service import LOGIN_COMPLETED, PASSENGERS_REVIEWED, ReadinessService
from services.reminder_service import ReminderService
from services.wallet_readiness_service import WalletReadinessService, WalletState, recommended_balance
from tests.helpers import Recorder, fake_clock, memory_db


# ---------- notifications ----------
def test_duplicate_notifications_are_suppressed_and_persisted():
    db, bus, clock = memory_db(), EventBus(), fake_clock()
    cfg = NotificationConfig(dedupe_window_seconds=300)
    svc = NotificationService(NotificationRepository(db), bus, clock, lambda: cfg)
    delivered = []
    svc.add_sink(lambda n, d, s: delivered.append((n.title, d, s)))
    assert svc.notify(NotificationKind.SYSTEM, "A", sound=True)
    assert svc.notify(NotificationKind.SYSTEM, "A") is None
    assert svc.suppressed_count == 1
    # survives restart: a fresh service sees the stored notification
    svc2 = NotificationService(NotificationRepository(db), bus, clock, lambda: cfg)
    assert svc2.notify(NotificationKind.SYSTEM, "A") is None
    clock.advance(301)
    assert svc2.notify(NotificationKind.SYSTEM, "A")
    assert delivered == [("A", True, True)]


def test_notification_settings_respected_and_broken_sink_isolated():
    db, bus, clock = memory_db(), EventBus(), fake_clock()
    cfg = NotificationConfig(desktop_enabled=False, sound_enabled=False)
    svc = NotificationService(NotificationRepository(db), bus, clock, lambda: cfg)
    got = []
    svc.add_sink(lambda *a: 1 / 0)
    svc.add_sink(lambda n, d, s: got.append((d, s)))
    svc.notify(NotificationKind.SYSTEM, "x", sound=True)
    assert got == [(False, False)]
    assert svc.unread_count() == 1
    svc.mark_read()
    assert svc.unread_count() == 0


# ---------- brave ----------
WIN_ENV = {"PROGRAMFILES": r"C:\Program Files", "PROGRAMFILES(X86)": r"C:\Program Files (x86)",
           "LOCALAPPDATA": r"C:\Users\raj\AppData\Local"}


def brave(exists=(), procs=(), override="", platform="win32", registry=(), launcher=None, focuser=None):
    bus = EventBus()
    calls = []
    svc = BraveService(bus, path_override=lambda: override, platform=platform, env=WIN_ENV,
                       file_exists=lambda p: p in exists, which=lambda c: None, registry_reader=lambda: list(registry),
                       process_lister=lambda: list(procs),
                       launcher=launcher or (lambda args, **kw: calls.append((args, kw))),
                       focuser=focuser or (lambda pids: True))
    return svc, bus, calls


def test_brave_detects_standard_locations_in_order():
    local = r"C:\Users\raj\AppData\Local\BraveSoftware\Brave-Browser\Application\brave.exe"
    pf = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
    svc, _, _ = brave(exists={local})
    assert svc.detect().path == local and svc.detect().source == "%LOCALAPPDATA%"
    svc, _, _ = brave(exists={local, pf})
    assert svc.detect().path == pf


def test_brave_override_and_registry():
    svc, _, _ = brave(exists={r"D:\Apps\brave.exe"}, override=r'"D:\Apps\brave.exe"')
    assert svc.detect().source == "settings"
    svc, _, _ = brave(exists={r"E:\B\brave.exe"}, registry=[r"E:\B\brave.exe"])
    assert svc.detect().source == "registry"
    svc, _, _ = brave()
    assert svc.detect() is None


def test_brave_launch_uses_normal_profile_without_automation_flags():
    pf = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
    svc, _, calls = brave(exists={pf})
    svc.launch("https://www.irctc.co.in/nget/train-search")
    args, kw = calls[0]
    assert args == [pf, "https://www.irctc.co.in/nget/train-search"]
    assert not any(a.startswith("--") for a in args)
    assert kw["creationflags"]
    with pytest.raises(Exception):
        svc.launch("http://insecure.example")


def test_brave_launch_without_install_raises():
    from core.exceptions import BrowserNotFoundError
    svc, _, _ = brave()
    with pytest.raises(BrowserNotFoundError):
        svc.launch()


def test_brave_state_transitions_and_disconnect_event():
    pf = r"C:\Program Files\BraveSoftware\Brave-Browser\Application\brave.exe"
    procs = [(10, "brave.exe")]
    svc, bus, _ = brave(exists={pf}, procs=procs)
    rec = Recorder(bus)
    assert svc.poll() is BrowserState.RUNNING
    procs.clear()
    assert svc.poll() is BrowserState.NOT_RUNNING
    assert len(rec.of(EventType.BROWSER_DISCONNECTED)) == 1
    assert svc.poll() is BrowserState.NOT_RUNNING
    assert len(rec.of(EventType.BROWSER_STATE_CHANGED)) == 2


def test_brave_process_listing_failure_is_unknown():
    def boom():
        raise RuntimeError

    svc = BraveService(EventBus(), platform="win32", env=WIN_ENV, file_exists=lambda p: False,
                       registry_reader=lambda: [], process_lister=boom)
    assert svc.poll() is BrowserState.UNKNOWN


def test_bring_to_foreground():
    svc, _, _ = brave(procs=[(5, "brave.exe")])
    assert svc.bring_to_foreground()[0]
    svc, _, _ = brave(procs=[])
    assert not svc.bring_to_foreground()[0]
    svc, _, _ = brave(procs=[(5, "brave")], platform="linux")
    ok, msg = svc.bring_to_foreground()
    assert not ok and "Windows" in msg


# ---------- passengers / codec / logs ----------
def test_passenger_crud_and_names_encrypted_at_rest_marker():
    db = memory_db()
    svc = PassengerService(PassengerRepository(db, SecureCodec(use_dpapi=False)), EventBus())
    p = svc.add(Passenger("Raj Kumar", 22, Gender.MALE))
    raw = db.query_one("SELECT name_enc FROM passengers")["name_enc"]
    assert raw == "plain:Raj Kumar"
    p2 = svc.update(Passenger("Raj K", 23, Gender.MALE, id=p.id))
    assert p2.age == 23 and svc.list()[0].name == "Raj K"
    for i in range(4):
        svc.add(Passenger(f"Person {chr(65 + i)}", 30, Gender.FEMALE))
    assert any("at most 4" in w for w in svc.warnings())
    assert svc.delete(p.id) and not svc.delete(p.id)


def test_log_redaction_masks_pii(tmp_path):
    r = Redactor()
    r.register("Raj Kumar", "Raj", "Kumar")
    text = r.redact("booking for Raj Kumar, mail raj@example.com, phone +91 9876543210, otp: 123456, password=hunter2")
    for leaked in ["Raj", "Kumar", "raj@example.com", "9876543210", "123456", "hunter2"]:
        assert leaked not in text
    rec = logging.LogRecord("x", logging.INFO, "", 0, "user %s", ("Kumar",), None)
    RedactionFilter(r).filter(rec)
    assert "Kumar" not in JsonFormatter().format(rec)


# ---------- config / recovery ----------
def test_config_roundtrip_floor_and_corruption(tmp_path):
    path = tmp_path / "config.json"
    m = ConfigManager(path)
    m.config.monitoring.poll_interval_seconds = 5
    m.save()
    assert ConfigManager(path).config.monitoring.poll_interval_seconds == HARD_MIN_POLL_SECONDS
    data = json.loads(path.read_text())
    data["monitoring"]["poll_interval_seconds"] = "fast"
    data["unknown"] = 1
    path.write_text(json.dumps(data))
    assert ConfigManager(path).config.monitoring.poll_interval_seconds == 300
    path.write_text("{broken")
    assert isinstance(ConfigManager(path).config, AppConfig)
    assert (tmp_path / "config.corrupt.json").exists()


def test_dotenv_does_not_override(tmp_path):
    f = tmp_path / ".env"
    f.write_text("# c\nA=1\nB='two'\n")
    env = {"A": "keep"}
    load_dotenv(f, env)
    assert env == {"A": "keep", "B": "two"}


def test_session_guard_detects_unclean_exit(tmp_path):
    g = SessionGuard(tmp_path / "s.json")
    assert g.begin() is False
    g2 = SessionGuard(tmp_path / "s.json")  # previous never called end()
    assert g2.begin() is True
    g2.end()
    assert SessionGuard(tmp_path / "s.json").begin() is False


def test_crash_report_written(tmp_path):
    try:
        raise ValueError("bad")
    except ValueError as e:
        p = write_crash_report(tmp_path, type(e), e, e.__traceback__)
    assert "ValueError: bad" in p.read_text()


# ---------- readiness / wallet / reminders ----------
def build_readiness(procs=()):
    db, bus, clock = memory_db(), EventBus(), fake_clock()
    sched = TatkalScheduler(clock)
    journeys = JourneyService(JourneyRepository(db), sched, bus)
    passengers = PassengerService(PassengerRepository(db, SecureCodec(False)), bus)
    b, _, _ = brave(procs=procs)
    b._bus = bus
    state = StateRepository(db)
    wallet = WalletReadinessService(state, bus, clock)
    ncfg = NotificationConfig()
    r = ReadinessService(state, journeys, passengers, b, wallet, lambda: ncfg, bus, clock)
    return r, journeys, passengers, wallet, b, clock, bus


def test_readiness_sources_and_manual_invalidation():
    r, journeys, passengers, wallet, b, clock, bus = build_readiness(procs=[(1, "brave.exe")])
    items = {i.id: i for i in r.items()}
    assert items["journey_prepared"].state is CheckState.PENDING
    assert items[LOGIN_COMPLETED].source is CheckSource.MANUAL and not items[LOGIN_COMPLETED].done
    journeys.save(Journey("12951", "ST", "NDLS", date(2026, 11, 20), TravelClass.AC_3_TIER))
    passengers.add(Passenger("Raj Kumar", 22, Gender.MALE))
    r.confirm(PASSENGERS_REVIEWED)
    r.confirm(LOGIN_COMPLETED)
    b.poll()
    items = {i.id: i for i in r.items()}
    assert items[PASSENGERS_REVIEWED].done and items[LOGIN_COMPLETED].done
    assert items["browser_opened"].source is CheckSource.AUTO_DETECTED and items["browser_opened"].done
    # editing passengers invalidates the review
    p = passengers.list()[0]
    passengers.update(Passenger("Raj Kumar", 23, Gender.MALE, id=p.id))
    assert {i.id: i for i in r.items()}[PASSENGERS_REVIEWED].state is CheckState.EXPIRED
    # login confirmation expires after 2h
    clock.advance(2 * 3600 + 1)
    assert {i.id: i for i in r.items()}[LOGIN_COMPLETED].state is CheckState.EXPIRED
    with pytest.raises(ValueError):
        r.confirm("browser_opened")


def test_browser_close_clears_login_confirmation():
    r, journeys, *_ , b, clock, bus = build_readiness()
    r.confirm(LOGIN_COMPLETED)
    bus.publish(EventType.BROWSER_DISCONNECTED)
    assert {i.id: i for i in r.items()}[LOGIN_COMPLETED].state is CheckState.PENDING


def test_wallet_status_lifecycle():
    r, _, _, wallet, _, clock, _ = build_readiness()
    assert wallet.status().state is WalletState.NOT_CHECKED
    wallet.set_estimated_fare(2340)
    assert wallet.status().recommended_balance == recommended_balance(2340) == 2600
    wallet.mark_checked()
    assert wallet.status().state is WalletState.CHECKED
    assert "Not verified" in wallet.status().message
    clock.advance(13 * 3600)
    assert wallet.status().state is WalletState.EXPIRED


def test_reminders_fire_once_inside_window_and_opening():
    clock = fake_clock(2026, 11, 19, 9, 29)  # AC opens 10:00
    bus = EventBus()
    sched = TatkalScheduler(clock)
    sched.set_journey(Journey("12951", "ST", "NDLS", date(2026, 11, 20), TravelClass.AC_3_TIER))
    cfg = NotificationConfig(reminder_minutes=[30, 10, 2])
    rem = ReminderService(sched, bus, clock, lambda: cfg)
    rec = Recorder(bus)
    for _ in range(31 * 60 + 5):
        rem.tick()
        clock.advance(1)
    assert [e.payload["minutes"] for e in rec.of(EventType.TATKAL_REMINDER)] == [30, 10, 2]
    assert len(rec.of(EventType.TATKAL_OPENED)) == 1


def test_reminder_not_fired_late_after_restart():
    clock = fake_clock(2026, 11, 19, 9, 55)
    bus = EventBus()
    sched = TatkalScheduler(clock)
    sched.set_journey(Journey("12951", "ST", "NDLS", date(2026, 11, 20), TravelClass.AC_3_TIER))
    rem = ReminderService(sched, bus, clock, lambda: NotificationConfig(reminder_minutes=[10]))
    rec = Recorder(bus)
    rem.tick()
    assert not rec.of(EventType.TATKAL_REMINDER)


def test_clock_jump_creates_warning_notification():
    clock = fake_clock()
    bus = EventBus()
    db = memory_db()
    notes = NotificationService(NotificationRepository(db), bus, clock, lambda: NotificationConfig())
    AlertService(bus, notes)
    sched = TatkalScheduler(clock)
    rem = ReminderService(sched, bus, clock, lambda: NotificationConfig())
    rem.tick()
    clock.jump_wall(3600)
    rem.tick()
    assert notes.recent()[0].title == "System clock changed"
