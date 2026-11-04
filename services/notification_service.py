"""Notification engine.

All alerts go through :meth:`NotificationService.notify`, which

1. suppresses duplicates (same ``dedupe_key`` inside the dedupe window; the
   check also consults the database, so it survives restarts),
2. stores the notification (in-app notification centre),
3. publishes ``NOTIFICATION_CREATED`` on the event bus,
4. hands it to registered sinks (Windows desktop toast, sound), honouring the
   user's settings.

Sinks are plain callables so this module has no GUI dependency.
"""

from __future__ import annotations

import logging
import threading
from datetime import datetime
from typing import Callable

from core.clock import Clock
from core.config import NotificationConfig
from core.event_bus import EventBus, EventType
from database.repositories.notification_repository import NotificationRepository
from models.notification import Notification, NotificationKind, Severity

log = logging.getLogger(__name__)

Sink = Callable[[Notification, bool, bool], None]  # (notification, show_desktop, play_sound)


class NotificationService:
    def __init__(
        self,
        repo: NotificationRepository,
        bus: EventBus,
        clock: Clock,
        config: Callable[[], NotificationConfig],
    ):
        self._repo = repo
        self._bus = bus
        self._clock = clock
        self._config = config
        self._sinks: list[Sink] = []
        self._recent: dict[str, datetime] = {}
        self._lock = threading.Lock()
        self.suppressed_count = 0

    def add_sink(self, sink: Sink) -> None:
        self._sinks.append(sink)

    def notify(
        self,
        kind: NotificationKind,
        title: str,
        body: str = "",
        severity: Severity = Severity.INFO,
        dedupe_key: str | None = None,
        sound: bool = False,
        desktop: bool = True,
    ) -> Notification | None:
        key = dedupe_key or f"{kind.value}:{title}"
        cfg = self._config()
        now = self._clock.now_utc()
        with self._lock:
            last = self._recent.get(key) or self._repo.last_created_at(key)
            if last is not None and 0 <= (now - last).total_seconds() < cfg.dedupe_window_seconds:
                self.suppressed_count += 1
                log.debug("duplicate notification suppressed", extra={"dedupe_key": key})
                return None
            self._recent[key] = now
            if len(self._recent) > 500:
                self._recent = dict(sorted(self._recent.items(), key=lambda kv: kv[1])[-250:])
        notification = self._repo.add(
            Notification(kind=kind, severity=severity, title=title, body=body, dedupe_key=key, created_at=now)
        )
        log.info("notification", extra={"kind": kind.value, "severity": severity.value})
        self._bus.publish(EventType.NOTIFICATION_CREATED, notification=notification)
        show_desktop = desktop and cfg.desktop_enabled
        play_sound = sound and cfg.sound_enabled
        for sink in list(self._sinks):
            try:
                sink(notification, show_desktop, play_sound)
            except Exception:
                log.exception("notification sink failed")
        return notification

    def recent(self, limit: int = 200) -> list[Notification]:
        return self._repo.recent(limit)

    def unread_count(self) -> int:
        return self._repo.unread_count()

    def mark_read(self, notification_id: int | None = None) -> None:
        self._repo.mark_read(notification_id)
        self._bus.publish(EventType.NOTIFICATION_CREATED, notification=None)

    def clear(self) -> None:
        self._repo.clear()
        with self._lock:
            self._recent.clear()
        self._bus.publish(EventType.NOTIFICATION_CREATED, notification=None)
