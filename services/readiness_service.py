"""Booking readiness checklist.

Each item states where its status comes from:

* DERIVED: computed from TatkalMate's own data (a valid journey is saved).
* AUTO_DETECTED: observed locally (a Brave process is running). This never
  implies the IRCTC page is open or that you are logged in.
* MANUAL: only the user can confirm it. TatkalMate shows it as "confirmed by
  you", never as verified, and confirmations expire or reset when the journey,
  the passengers or the browser session change.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Callable

from core.clock import Clock
from core.config import NotificationConfig
from core.event_bus import Event, EventBus, EventType
from database.repositories.state_repository import StateRepository
from models.readiness import CheckSource, CheckState, ReadinessItem
from services.brave_service import BraveService, BrowserState
from services.journey_service import JourneyService
from services.passenger_service import PassengerService
from services.wallet_readiness_service import WalletReadinessService, WalletState

PASSENGERS_REVIEWED = "passengers_reviewed"
LOGIN_COMPLETED = "login_completed"
MANUAL_ITEMS = {PASSENGERS_REVIEWED, LOGIN_COMPLETED}

VALIDITY = {
    PASSENGERS_REVIEWED: timedelta(hours=24),
    LOGIN_COMPLETED: timedelta(hours=2),  # IRCTC sessions time out; confirm close to opening
}


class ReadinessService:
    def __init__(
        self,
        state: StateRepository,
        journeys: JourneyService,
        passengers: PassengerService,
        brave: BraveService,
        wallet: WalletReadinessService,
        notification_config: Callable[[], NotificationConfig],
        bus: EventBus,
        clock: Clock,
    ):
        self._state = state
        self._journeys = journeys
        self._passengers = passengers
        self._brave = brave
        self._wallet = wallet
        self._notification_config = notification_config
        self._bus = bus
        self._clock = clock
        bus.subscribe(EventType.BROWSER_DISCONNECTED, self._on_browser_closed)

    def _context(self, item: str) -> str:
        journey = self._journeys.active()
        jkey = journey.context_key if journey else "none"
        if item == PASSENGERS_REVIEWED:
            return f"{jkey}#{self._passengers.fingerprint()}"
        return jkey

    def confirm(self, item: str) -> None:
        if item not in MANUAL_ITEMS:
            raise ValueError(f"{item} cannot be confirmed manually")
        self._state.confirm(item, self._context(item), self._clock.now_utc())
        self._bus.publish(EventType.READINESS_CHANGED)

    def unconfirm(self, item: str) -> None:
        self._state.unconfirm(item)
        self._bus.publish(EventType.READINESS_CHANGED)

    def _on_browser_closed(self, _event: Event) -> None:
        # The IRCTC session lived in that browser; the login confirmation no longer holds.
        if self._state.confirmation(LOGIN_COMPLETED):
            self.unconfirm(LOGIN_COMPLETED)

    def _manual(self, item: str, label: str, pending_detail: str) -> ReadinessItem:
        conf = self._state.confirmation(item)
        if conf is None:
            return ReadinessItem(item, label, CheckState.PENDING, CheckSource.MANUAL, pending_detail,
                                 user_confirmable=True)
        context, when = conf
        if context != self._context(item):
            return ReadinessItem(item, label, CheckState.EXPIRED, CheckSource.MANUAL,
                                 "Details changed since you confirmed. Please review again.", when, True)
        if self._clock.now_utc() - when > VALIDITY[item]:
            hours = int(VALIDITY[item].total_seconds() // 3600)
            return ReadinessItem(item, label, CheckState.EXPIRED, CheckSource.MANUAL,
                                 f"Confirmed over {hours} h ago. Please confirm again.", when, True)
        return ReadinessItem(item, label, CheckState.DONE, CheckSource.MANUAL,
                             "Not verified by TatkalMate.", when, True)

    def items(self) -> list[ReadinessItem]:
        out: list[ReadinessItem] = []
        journey = self._journeys.active()
        out.append(
            ReadinessItem("journey_prepared", "Journey details prepared",
                          CheckState.DONE if journey else CheckState.PENDING, CheckSource.DERIVED,
                          f"Train {journey.train_number} on {journey.journey_date:%d %b %Y}" if journey
                          else "Save a journey on the Journey page.")
        )
        reviewed = self._manual(PASSENGERS_REVIEWED, "Passenger details reviewed",
                                "Review passengers, then confirm.")
        warnings = self._passengers.warnings()
        if warnings and reviewed.state is CheckState.DONE:
            reviewed = ReadinessItem(reviewed.id, reviewed.label, CheckState.PENDING, reviewed.source,
                                     warnings[0], reviewed.confirmed_at, True)
        out.append(reviewed)

        browser_state = self._brave.state
        out.append(
            ReadinessItem("browser_opened", "Brave browser opened",
                          CheckState.DONE if browser_state is BrowserState.RUNNING else CheckState.PENDING,
                          CheckSource.AUTO_DETECTED,
                          {BrowserState.RUNNING: "Brave process detected. This does not confirm the IRCTC page or login.",
                           BrowserState.NOT_RUNNING: "Brave is not running.",
                           BrowserState.NOT_INSTALLED: "Brave was not found. Set its path in Settings.",
                           BrowserState.UNKNOWN: "Cannot detect running processes on this system."}[browser_state])
        )
        out.append(self._manual(LOGIN_COMPLETED, "Manual IRCTC login completed",
                                "Log in on IRCTC in Brave yourself, then confirm here."))
        wallet = self._wallet.status()
        out.append(
            ReadinessItem(
                "wallet_checked", "eWallet balance checked",
                {WalletState.CHECKED: CheckState.DONE, WalletState.EXPIRED: CheckState.EXPIRED,
                 WalletState.NOT_CHECKED: CheckState.PENDING}[wallet.state],
                CheckSource.MANUAL, wallet.message, wallet.checked_at, True,
            )
        )
        cfg = self._notification_config()
        enabled = cfg.opening_reminders_enabled and bool(cfg.reminder_minutes)
        out.append(
            ReadinessItem("reminder_enabled", "Booking opening reminder enabled",
                          CheckState.DONE if enabled else CheckState.PENDING, CheckSource.DERIVED,
                          ("Alerts at " + ", ".join(f"T-{m}" for m in cfg.reminder_minutes)
                           + (f", refresh at T-{cfg.refresh_reminder_minutes}" if cfg.refresh_reminder_minutes else "")
                           + ("" if cfg.desktop_enabled else " (in-app only: desktop alerts are off)"))
                          if enabled else "Turn on opening reminders in Settings.")
        )
        return out
