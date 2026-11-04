"""Composition root: builds and wires every service. GUI-independent, so the
whole backend can be started in tests without Qt."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.async_runtime import AsyncRuntime
from core.clock import Clock, SystemClock
from core.time_sync import OffsetClock
from core.config import ConfigManager
from core.event_bus import Event, EventBus, EventType
from core.paths import AppPaths
from core.scheduler import TatkalScheduler
from core.secure_codec import SecureCodec
from database.connection import Database
from database.models import migrate
from database.repositories.availability_repository import AvailabilityRepository
from database.repositories.journey_repository import JourneyRepository
from database.repositories.notification_repository import NotificationRepository
from database.repositories.passenger_repository import PassengerRepository
from database.repositories.state_repository import StateRepository
from providers import create_provider
from providers.base_provider import AvailabilityProvider
from services.alert_service import AlertService
from services.availability_service import AvailabilityService
from services.brave_service import BraveService, BrowserState
from services.health_service import HealthService
from services.journey_service import JourneyService
from services.notification_service import NotificationService
from services.passenger_service import PassengerService
from services.readiness_service import ReadinessService
from services.reminder_service import ReminderService
from services.time_sync_service import TimeSyncService
from services.update_service import UpdateService
from services.wallet_readiness_service import WalletReadinessService

log = logging.getLogger(__name__)


@dataclass
class AppContext:
    paths: AppPaths
    config: ConfigManager
    clock: Clock
    bus: EventBus
    runtime: AsyncRuntime
    db: Database
    codec: SecureCodec
    scheduler: TatkalScheduler
    journeys: JourneyService
    passengers: PassengerService
    notifications: NotificationService
    alerts: AlertService
    availability: AvailabilityService
    brave: BraveService
    wallet: WalletReadinessService
    readiness: ReadinessService
    health: HealthService
    reminders: ReminderService
    time_sync: TimeSyncService | None = None
    updates: UpdateService | None = None
    background_started: bool = False

    def start_background(self) -> None:
        if self.background_started:
            return
        self.runtime.start()
        cfg = self.config
        self.runtime.spawn("reminders", self.reminders.run_loop)
        if self.time_sync is not None:
            self.runtime.spawn("time-sync", self.time_sync.run_loop)
        self.runtime.spawn("browser-watch", lambda: self.brave.watch(lambda: cfg.config.browser.watch_interval_seconds))
        self.runtime.spawn("health", self.health.run_loop)
        self.background_started = True
        log.info("background services started")

    def shutdown(self) -> None:
        log.info("shutting down")
        try:
            self.availability.stop()
            self.runtime.stop()
        finally:
            self.db.close()


def build_context(
    paths: AppPaths,
    clock: Clock | None = None,
    provider: AvailabilityProvider | None = None,
    brave: BraveService | None = None,
    codec: SecureCodec | None = None,
) -> AppContext:
    clock = clock or OffsetClock(SystemClock())
    config = ConfigManager(paths.config_file)
    bus = EventBus()
    runtime = AsyncRuntime(bus)
    db = Database(paths.database_file)
    migrate(db)
    codec = codec or SecureCodec()
    state_repo = StateRepository(db)

    scheduler = TatkalScheduler(clock)
    journeys = JourneyService(JourneyRepository(db), scheduler, bus)
    journeys.load()
    passengers = PassengerService(PassengerRepository(db, codec), bus)
    notifications = NotificationService(NotificationRepository(db), bus, clock, lambda: config.config.notifications)
    alerts = AlertService(bus, notifications)
    mon_cfg = config.config.monitoring
    provider = provider or create_provider(mon_cfg.provider, clock, mon_cfg.request_timeout_seconds)
    availability = AvailabilityService(provider, AvailabilityRepository(db), bus, clock, mon_cfg, runtime)
    brave = brave or BraveService(bus, path_override=lambda: config.config.browser.brave_path_override, clock=clock)
    brave.detect()
    wallet = WalletReadinessService(state_repo, bus, clock)
    readiness = ReadinessService(state_repo, journeys, passengers, brave, wallet,
                                 lambda: config.config.notifications, bus, clock)
    health = HealthService(db, paths, codec, lambda: availability.provider, lambda: config.config.network,
                           bus, clock, runtime, brave)
    reminders = ReminderService(scheduler, bus, clock, lambda: config.config.notifications,
                                lambda: config.config.browser)
    time_sync = (TimeSyncService(clock, scheduler, bus, lambda: config.config.time_sync)
                 if isinstance(clock, OffsetClock) else None)

    def on_auto_launch(e: Event) -> None:
        from core.exceptions import BrowserError
        from models.notification import NotificationKind, Severity

        if brave.state is BrowserState.RUNNING:
            notifications.notify(NotificationKind.BROWSER, "Brave is open",
                                 f"Tatkal opens in {e.payload['minutes']} min. Make sure IRCTC is open and you're "
                                 "logged in.", Severity.INFO, "auto-launch")
            return
        try:
            brave.launch(config.config.browser.irctc_url)
            notifications.notify(NotificationKind.BROWSER, "Brave opened on IRCTC",
                                 f"Tatkal opens in {e.payload['minutes']} min. Log in now.", Severity.WARNING,
                                 "auto-launch", sound=True)
        except BrowserError as exc:
            notifications.notify(NotificationKind.BROWSER, "Could not open Brave automatically", str(exc),
                                 Severity.CRITICAL, "auto-launch", sound=True)

    def on_opened(_e: Event) -> None:
        if config.config.browser.focus_at_opening:
            brave.bring_to_foreground()

    bus.subscribe(EventType.BROWSER_AUTO_LAUNCH_DUE, on_auto_launch)
    bus.subscribe(EventType.TATKAL_OPENED, on_opened)

    def on_config(new_cfg) -> None:
        availability.config = new_cfg.monitoring
        if new_cfg.monitoring.provider != availability.provider.info.id and not availability.is_monitoring:
            availability.set_provider(create_provider(new_cfg.monitoring.provider, clock,
                                                      new_cfg.monitoring.request_timeout_seconds))
        brave.detect()
        bus.publish(EventType.CONFIG_CHANGED)
        bus.publish(EventType.READINESS_CHANGED)

    config.on_change(on_config)

    def on_network(e: Event) -> None:
        if e.payload.get("online"):
            availability.nudge()

    bus.subscribe(EventType.NETWORK_CHANGED, on_network)
    for et in (EventType.JOURNEY_CHANGED, EventType.PASSENGERS_CHANGED, EventType.BROWSER_STATE_CHANGED):
        bus.subscribe(et, lambda _e: bus.publish(EventType.READINESS_CHANGED))

    return AppContext(paths, config, clock, bus, runtime, db, codec, scheduler, journeys, passengers,
                      notifications, alerts, availability, brave, wallet, readiness, health, reminders, time_sync,
                      UpdateService(paths, scheduler, clock))
