"""Marshals backend events and callbacks onto the Qt GUI thread."""

from __future__ import annotations

import concurrent.futures
import logging
from typing import Any, Callable

from PySide6.QtCore import QObject, Signal, Slot

from core.event_bus import Event, EventBus

log = logging.getLogger(__name__)


class UiBridge(QObject):
    event = Signal(object)
    _invoke = Signal(object)

    def __init__(self, bus: EventBus):
        super().__init__()
        self._invoke.connect(self._run)
        # Emitting from a worker thread queues delivery to this object's (GUI) thread.
        self._unsubscribe = bus.subscribe(None, lambda e: self.event.emit(e))

    @Slot(object)
    def _run(self, fn: Callable[[], Any]) -> None:
        try:
            fn()
        except Exception:
            log.exception("UI callback failed")

    def post(self, fn: Callable[[], Any]) -> None:
        """Run ``fn`` on the GUI thread (safe to call from any thread)."""
        self._invoke.emit(fn)

    def on_done(self, future: concurrent.futures.Future, ok: Callable[[Any], None],
                err: Callable[[BaseException], None] | None = None) -> None:
        def done(f: concurrent.futures.Future) -> None:
            if f.cancelled():
                return
            exc = f.exception()
            if exc is not None:
                if err:
                    self.post(lambda: err(exc))
                else:
                    log.error("background call failed: %s", exc)
            else:
                result = f.result()
                self.post(lambda: ok(result))

        future.add_done_callback(done)

    def close(self) -> None:
        self._unsubscribe()


def is_event(e: Event, *types) -> bool:
    return e.type in types
