"""Thread-safe publish/subscribe event bus.

Handlers run synchronously on the publisher's thread. The Qt layer subscribes
through ``app.bridge.UiBridge``, which re-emits events on the GUI thread, so
services never touch widgets directly.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable

log = logging.getLogger(__name__)


class EventType(str, Enum):
    JOURNEY_CHANGED = "journey.changed"
    PASSENGERS_CHANGED = "passengers.changed"
    READINESS_CHANGED = "readiness.changed"
    WALLET_CHANGED = "wallet.changed"
    BROWSER_STATE_CHANGED = "browser.state_changed"
    BROWSER_DISCONNECTED = "browser.disconnected"
    AVAILABILITY_RESULT = "availability.result"
    AVAILABILITY_CHANGED = "availability.changed"
    AVAILABILITY_STALE = "availability.stale"
    MONITOR_STATE_CHANGED = "monitor.state_changed"
    MONITOR_FAILURE = "monitor.failure"
    MONITOR_RECOVERED = "monitor.recovered"
    NOTIFICATION_CREATED = "notification.created"
    TATKAL_REMINDER = "tatkal.reminder"
    TATKAL_OPENED = "tatkal.opened"
    TATKAL_REFRESH_DUE = "tatkal.refresh_due"
    BROWSER_AUTO_LAUNCH_DUE = "browser.auto_launch_due"
    TIME_SYNCED = "time.synced"
    CLOCK_CHANGED = "clock.changed"
    NETWORK_CHANGED = "network.changed"
    HEALTH_UPDATED = "health.updated"
    CONFIG_CHANGED = "config.changed"
    TASK_FAILED = "runtime.task_failed"


@dataclass(frozen=True)
class Event:
    type: EventType
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


Handler = Callable[[Event], None]


class EventBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._handlers: dict[EventType | None, list[Handler]] = {}

    def subscribe(self, event_type: EventType | None, handler: Handler) -> Callable[[], None]:
        """Subscribe to one event type, or to every event when ``event_type``
        is None. Returns a function that unsubscribes."""
        with self._lock:
            self._handlers.setdefault(event_type, []).append(handler)

        def unsubscribe() -> None:
            with self._lock:
                handlers = self._handlers.get(event_type, [])
                if handler in handlers:
                    handlers.remove(handler)

        return unsubscribe

    def publish(self, event_type: EventType, **payload: Any) -> Event:
        event = Event(event_type, payload)
        with self._lock:
            handlers = list(self._handlers.get(event_type, [])) + list(
                self._handlers.get(None, [])
            )
        for handler in handlers:
            try:
                handler(event)
            except Exception:  # a broken subscriber must not break publishers
                log.exception("event handler failed", extra={"event": event_type.value})
        return event
