"""Composition root: builds and wires every service. GUI-independent, so the
whole backend can be started in tests without Qt."""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.async_runtime import AsyncRuntime
from core.clock import Clock, SystemClock
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
from services.brave_service import BraveService
from services.health_service import HealthService
from services.journey_service import JourneyService
from services.notification_service import NotificationService
from services.passenger_service import PassengerService
from services.readiness_service import ReadinessService
from services.reminder_service import ReminderService
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
    background_started: bool = False

    def start_background(self) -> None:
        if self.background_started:
            return
        self.runtime.start()
        cfg = self.config
        self.runtime.spawn("reminders", self.reminders.run_loop)
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
    clock = clock or SystemClock()
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
    reminders = ReminderService(scheduler, bus, clock, lambda: config.config.notifications)

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
                      notifications, alerts, availability, brave, wallet, readiness, health, reminders)
