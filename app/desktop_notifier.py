"""Desktop notification and sound sink.

Uses ``QSystemTrayIcon.showMessage``, which Windows 10/11 shows as a native
toast in the Action Center. Sound uses ``winsound`` on Windows and the Qt
beep elsewhere. Both degrade to no-ops when unavailable.
"""

from __future__ import annotations

import logging
import sys

from PySide6.QtCore import Qt
from PySide6.QtGui import QAction, QColor, QFont, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from app.bridge import UiBridge
from models.notification import Notification, Severity

log = logging.getLogger(__name__)


def app_icon() -> QIcon:
    pix = QPixmap(64, 64)
    pix.fill(Qt.GlobalColor.transparent)
    p = QPainter(pix)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QColor("#4C8DFF"))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawRoundedRect(4, 4, 56, 56, 14, 14)
    p.setPen(QColor("white"))
    f = QFont()
    f.setBold(True)
    f.setPixelSize(30)
    p.setFont(f)
    p.drawText(pix.rect(), Qt.AlignmentFlag.AlignCenter, "T")
    p.end()
    return QIcon(pix)


class DesktopNotifier:
    def __init__(self, bridge: UiBridge, on_show, on_quit):
        self._bridge = bridge
        self.tray: QSystemTrayIcon | None = None
        if QSystemTrayIcon.isSystemTrayAvailable():
            self.tray = QSystemTrayIcon(app_icon())
            self.tray.setToolTip("TatkalMate AI")
            menu = QMenu()
            show = QAction("Show TatkalMate", menu)
            show.triggered.connect(on_show)
            quit_ = QAction("Quit", menu)
            quit_.triggered.connect(on_quit)
            menu.addAction(show)
            menu.addSeparator()
            menu.addAction(quit_)
            self.tray.setContextMenu(menu)
            self.tray.activated.connect(lambda reason: on_show()
                                        if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
            self.tray.messageClicked.connect(on_show)
            self.tray.show()
            self._menu = menu
        else:
            log.info("system tray not available; desktop notifications disabled")

    @property
    def available(self) -> bool:
        return self.tray is not None

    def __call__(self, n: Notification, show_desktop: bool, play_sound: bool) -> None:
        # May be called from the asyncio thread: hop to the GUI thread.
        self._bridge.post(lambda: self._deliver(n, show_desktop, play_sound))

    def _deliver(self, n: Notification, show_desktop: bool, play_sound: bool) -> None:
        if show_desktop and self.tray is not None:
            icon = {Severity.CRITICAL: QSystemTrayIcon.MessageIcon.Critical,
                    Severity.WARNING: QSystemTrayIcon.MessageIcon.Warning}.get(
                n.severity, QSystemTrayIcon.MessageIcon.Information)
            self.tray.showMessage(n.title, n.body, icon, 8000)
        if play_sound:
            play_alert_sound(n.severity)

    def hide(self) -> None:
        if self.tray:
            self.tray.hide()


def play_alert_sound(severity: Severity) -> None:
    if sys.platform.startswith("win"):
        try:
            import winsound

            winsound.MessageBeep(winsound.MB_ICONHAND if severity is Severity.CRITICAL else winsound.MB_ICONEXCLAMATION)
            return
        except Exception:
            pass
    QApplication.beep()
