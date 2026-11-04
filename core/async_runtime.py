"""Background asyncio runtime.

An asyncio event loop runs on a dedicated daemon thread. The Qt GUI thread
submits coroutines with :meth:`AsyncRuntime.submit` and receives a
``concurrent.futures.Future``. Long-running tasks are started with
:meth:`AsyncRuntime.spawn`, which supervises them: an unexpected crash is
logged, published on the event bus and the task is restarted with backoff, so
background services never fail silently.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from typing import Any, Awaitable, Callable, Coroutine

from core.event_bus import EventBus, EventType

log = logging.getLogger(__name__)


class AsyncRuntime:
    def __init__(self, bus: EventBus | None = None):
        self._bus = bus
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._tasks: dict[str, asyncio.Task[Any]] = {}

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        if self._loop is None:
            raise RuntimeError("runtime not started")
        return self._loop

    @property
    def is_running(self) -> bool:
        return bool(self._thread and self._thread.is_alive() and self._loop and self._loop.is_running())

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="tatkalmate-async", daemon=True)
        self._thread.start()
        if not self._ready.wait(5):
            raise RuntimeError("async runtime failed to start")

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.set_exception_handler(self._on_loop_exception)
        self._loop = loop
        loop.call_soon(self._ready.set)
        try:
            loop.run_forever()
        finally:
            try:
                pending = asyncio.all_tasks(loop)
                for t in pending:
                    t.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
                loop.run_until_complete(loop.shutdown_asyncgens())
            finally:
                loop.close()

    def _on_loop_exception(self, loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        log.error("unhandled asyncio error: %s", context.get("message"), exc_info=context.get("exception"))

    def submit(self, coro: Coroutine[Any, Any, Any]) -> concurrent.futures.Future[Any]:
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def call(self, fn: Callable[[], Any]) -> None:
        self.loop.call_soon_threadsafe(fn)

    def spawn(
        self,
        name: str,
        factory: Callable[[], Awaitable[Any]],
        *,
        restart: bool = True,
        max_restart_delay: float = 60.0,
    ) -> None:
        """Start a supervised long-running task (replacing one with the same name)."""

        async def supervisor() -> None:
            delay = 1.0
            while True:
                try:
                    await factory()
                    return  # finished normally
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    log.exception("background task crashed", extra={"task": name})
                    if self._bus:
                        self._bus.publish(EventType.TASK_FAILED, task=name, error=str(exc))
                    if not restart:
                        return
                    await asyncio.sleep(delay)
                    delay = min(max_restart_delay, delay * 2)

        def start() -> None:
            old = self._tasks.pop(name, None)
            if old and not old.done():
                old.cancel()
            self._tasks[name] = self.loop.create_task(supervisor(), name=name)

        self.loop.call_soon_threadsafe(start)

    def cancel(self, name: str) -> None:
        def stop() -> None:
            task = self._tasks.pop(name, None)
            if task and not task.done():
                task.cancel()

        if self._loop and self._loop.is_running():
            self._loop.call_soon_threadsafe(stop)

    def task_names(self) -> list[str]:
        return [n for n, t in list(self._tasks.items()) if not t.done()]

    def stop(self, timeout: float = 5.0) -> None:
        if not self._loop or not self._thread:
            return
        loop = self._loop

        async def cancel_all() -> None:
            tasks = [t for t in asyncio.all_tasks(loop) if t is not asyncio.current_task()]
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        if loop.is_running():
            try:
                asyncio.run_coroutine_threadsafe(cancel_all(), loop).result(timeout)
            except Exception:
                log.warning("some background tasks did not stop in time")
            loop.call_soon_threadsafe(loop.stop)
        self._thread.join(timeout)
        self._tasks.clear()
