"""Turns domain events into user notifications.

This is the only place that decides what the user is told, which keeps the
"never claim unconfirmed availability" rule in one auditable spot.
"""

from __future__ import annotations

from core.event_bus import Event, EventBus, EventType
from models.availability import AvailabilityResult, AvailabilityStatus, Freshness
from models.notification import NotificationKind, Severity
from services.notification_service import NotificationService


def _tag(result: AvailabilityResult) -> str:
    return "[Simulated] " if result.simulated else ""


class AlertService:
    def __init__(self, bus: EventBus, notifications: NotificationService):
        self._n = notifications
        bus.subscribe(EventType.AVAILABILITY_CHANGED, self._on_availability_changed)
        bus.subscribe(EventType.AVAILABILITY_STALE, self._on_stale)
        bus.subscribe(EventType.MONITOR_FAILURE, self._on_monitor_failure)
        bus.subscribe(EventType.MONITOR_RECOVERED, self._on_monitor_recovered)
        bus.subscribe(EventType.BROWSER_DISCONNECTED, self._on_browser_disconnected)
        bus.subscribe(EventType.NETWORK_CHANGED, self._on_network)
        bus.subscribe(EventType.CLOCK_CHANGED, self._on_clock)
        bus.subscribe(EventType.TATKAL_REMINDER, self._on_reminder)
        bus.subscribe(EventType.TATKAL_OPENED, self._on_opened)
        bus.subscribe(EventType.TASK_FAILED, self._on_task_failed)

    def _on_availability_changed(self, e: Event) -> None:
        result: AvailabilityResult = e.payload["result"]
        freshness: Freshness = e.payload["freshness"]
        train = result.query.train_number
        text = result.describe()
        key = f"avail:{result.query.key}:{result.status.value}:{result.seats}"
        if freshness is Freshness.VERIFIED:
            if result.status is AvailabilityStatus.AVAILABLE:
                self._n.notify(NotificationKind.AVAILABILITY,
                               f"{_tag(result)}Seats available: {train} {result.query.travel_class.value}",
                               f"{text} (confirmed by {result.source}). Book on IRCTC in Brave.",
                               Severity.SUCCESS, key, sound=True)
            else:
                self._n.notify(NotificationKind.AVAILABILITY,
                               f"{_tag(result)}Availability changed: {train}",
                               f"Now {text} (confirmed by {result.source}).", Severity.INFO, key)
        else:
            label = {Freshness.CACHED: "cached", Freshness.STALE: "stale",
                     Freshness.UNCONFIRMED: "unconfirmed"}[freshness]
            self._n.notify(NotificationKind.AVAILABILITY,
                           f"{_tag(result)}Unverified change: {train}",
                           f"{result.source} reports {text}, but the data is {label}. "
                           "Treat it as unconfirmed and check IRCTC directly.",
                           Severity.WARNING, key, desktop=False)

    def _on_stale(self, e: Event) -> None:
        result: AvailabilityResult = e.payload["result"]
        self._n.notify(NotificationKind.MONITOR, "Availability data is stale",
                       f"No fresh data for train {result.query.train_number}. The last result is out of date.",
                       Severity.WARNING, f"stale:{result.query.key}")

    def _on_monitor_failure(self, e: Event) -> None:
        p = e.payload
        title = "Availability provider is not configured" if p.get("fatal") else "Availability monitoring is failing"
        body = (f"{p['provider']}: {p['error']}." if p.get("fatal")
                else f"{p['failures']} checks in a row failed ({p['error']}). Retrying with backoff.")
        self._n.notify(NotificationKind.MONITOR, title, body, Severity.CRITICAL, f"monitor-fail:{p['provider']}",
                       sound=True)

    def _on_monitor_recovered(self, e: Event) -> None:
        self._n.notify(NotificationKind.MONITOR, "Availability monitoring recovered",
                       f"{e.payload['provider']} is answering again.", Severity.SUCCESS,
                       f"monitor-ok:{e.payload['provider']}")

    def _on_browser_disconnected(self, _e: Event) -> None:
        self._n.notify(NotificationKind.BROWSER, "Brave was closed",
                       "Brave is no longer running. If that was unexpected, relaunch it from the Browser page "
                       "and log in to IRCTC again.", Severity.WARNING, "browser-closed", sound=True)

    def _on_network(self, e: Event) -> None:
        if e.payload["online"]:
            self._n.notify(NotificationKind.NETWORK, "Back online", "Internet connection restored.",
                           Severity.SUCCESS, "net-online")
        else:
            self._n.notify(NotificationKind.NETWORK, "Network connection lost",
                           "TatkalMate cannot reach the internet. Monitoring will retry automatically.",
                           Severity.CRITICAL, "net-offline", sound=True)

    def _on_clock(self, e: Event) -> None:
        delta = e.payload["delta_seconds"]
        self._n.notify(NotificationKind.CLOCK, "System clock changed",
                       f"The clock moved by {delta:+.0f} s (time change or resume from sleep). "
                       "The countdown has been recalculated. Make sure Windows time sync is on.",
                       Severity.WARNING, "clock-jump")

    def _on_reminder(self, e: Event) -> None:
        m = e.payload["minutes"]
        s = e.payload["schedule"]
        self._n.notify(NotificationKind.TATKAL_OPENING, f"{s.window.label} opens in {m} min",
                       f"Booking opens at {s.opens_at:%H:%M} IST on {s.booking_date:%d %b}. "
                       "Check that you're logged in to IRCTC and the wallet is ready.",
                       Severity.WARNING, f"remind:{s.opens_at.isoformat()}:{m}", sound=m <= 2)

    def _on_opened(self, e: Event) -> None:
        s = e.payload["schedule"]
        self._n.notify(NotificationKind.TATKAL_OPENING, f"{s.window.label} booking is open now",
                       "Complete the booking yourself on IRCTC in Brave.", Severity.CRITICAL,
                       f"open:{s.opens_at.isoformat()}", sound=True)

    def _on_task_failed(self, e: Event) -> None:
        self._n.notify(NotificationKind.SYSTEM, "A background service crashed",
                       f"{e.payload['task']}: {e.payload['error']}. It is restarting automatically.",
                       Severity.CRITICAL, f"task:{e.payload['task']}")
