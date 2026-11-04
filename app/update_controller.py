"""GUI side of the updater: periodic checks, notifications and the one-click install."""

from __future__ import annotations

import asyncio
import logging

from PySide6.QtCore import QObject, QTimer, Signal
from PySide6.QtWidgets import QApplication, QMessageBox

from app.bridge import UiBridge
from models.notification import NotificationKind, Severity
from services.container import AppContext
from services.update_service import ReleaseInfo

log = logging.getLogger(__name__)

FIRST_CHECK_MS = 10_000
CHECK_EVERY_MS = 6 * 3600 * 1000


class UpdateController(QObject):
    changed = Signal()  # status text / availability changed

    def __init__(self, ctx: AppContext, bridge: UiBridge, parent=None):
        super().__init__(parent)
        self.ctx = ctx
        self.bridge = bridge
        self.svc = ctx.updates
        self.status = "Not checked yet."
        self.busy = False
        self.available: ReleaseInfo | None = None
        if self.svc is not None:
            try:
                self.svc.cleanup()
            except OSError:
                pass
        self.timer = QTimer(self)
        self.timer.setInterval(CHECK_EVERY_MS)
        self.timer.timeout.connect(lambda: self.check(silent=True))

    def start(self) -> None:
        if self.svc is None or not self.ctx.config.config.ui.check_updates:
            return
        QTimer.singleShot(FIRST_CHECK_MS, lambda: self.check(silent=True))
        self.timer.start()

    def _set(self, status: str) -> None:
        self.status = status
        self.changed.emit()

    def check(self, silent: bool = False) -> None:
        if self.svc is None or self.busy or not self.ctx.runtime.is_running:
            return
        self.busy = True
        self._set("Checking GitHub for updates…")
        fut = self.ctx.runtime.submit(asyncio.to_thread(self.svc.check))

        def ok(info: ReleaseInfo | None) -> None:
            self.busy = False
            self.available = info
            if info is None:
                self._set("You're on the latest version.")
                return
            self._set(f"Version {info.version} is available.")
            blocked = self.svc.blocked_reason()
            self.ctx.notifications.notify(
                NotificationKind.SYSTEM, f"Update available: TatkalMate {info.version}",
                "Install it from Settings → Updates." + (f" {blocked}" if blocked else ""),
                Severity.INFO, dedupe_key=f"update:{info.version}")

        def err(exc: BaseException) -> None:
            self.busy = False
            self._set(f"Update check failed: {exc}")
            if not silent:
                log.warning("update check failed")

        self.bridge.on_done(fut, ok, err)

    def install(self, parent_widget=None) -> None:
        info = self.available
        if self.svc is None or info is None or self.busy:
            return
        reason = self.svc.can_install()
        if reason:
            self._set(reason)
            return
        self.busy = True
        self._set(f"Downloading {info.version}…")
        progress = lambda m: self.bridge.post(lambda: self._set(m))  # noqa: E731
        fut = self.ctx.runtime.submit(asyncio.to_thread(self.svc.download, info, progress))

        def ok(new_dir) -> None:
            self.busy = False
            reason = self.svc.can_install()  # re-check: the Tatkal window may have started meanwhile
            if reason:
                self._set(reason)
                return
            answer = QMessageBox.question(
                parent_widget, "Install update",
                f"TatkalMate {info.version} is downloaded and verified.\n\nRestart now to install it? "
                "Your journey, passengers, settings and history are kept.")
            if answer != QMessageBox.StandardButton.Yes:
                self._set(f"{info.version} is ready. Click install again to restart.")
                return
            try:
                self.svc.start_install(new_dir)
            except Exception as exc:
                self._set(f"Could not start the installer: {exc}")
                return
            self._set("Restarting to finish the update…")
            QApplication.quit()

        def err(exc: BaseException) -> None:
            self.busy = False
            self._set(f"Update failed: {exc}")

        self.bridge.on_done(fut, ok, err)
