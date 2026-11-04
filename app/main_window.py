from __future__ import annotations

import logging

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QCloseEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QStackedWidget,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from app.bridge import UiBridge
from app.desktop_notifier import DesktopNotifier, app_icon
from app.navigation import NAV_ITEMS, index_of
from app.views.browser_view import BrowserView
from app.views.dashboard_view import DashboardView
from app.views.diagnostics_view import DiagnosticsView
from app.views.journey_view import JourneyView
from app.views.monitoring_view import MonitoringView
from app.views.notifications_view import NotificationsView
from app.views.passengers_view import PassengersView
from app.views.preparation_view import PreparationView
from app.views.quick_panel import QuickPanel
from app.views.settings_view import SettingsView
from core.event_bus import Event, EventType
from core.version import APP_NAME, __version__
from services.container import AppContext

log = logging.getLogger(__name__)


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext, bridge: UiBridge):
        super().__init__()
        self.ctx = ctx
        self.bridge = bridge
        self._quitting = False
        self.setWindowTitle(f"{APP_NAME}")
        self.setWindowIcon(app_icon())
        self.setMinimumSize(QSize(1100, 720))
        self.resize(1320, 860)

        root = QWidget()
        layout = QHBoxLayout(root)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        sidebar = QWidget()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(230)
        sl = QVBoxLayout(sidebar)
        sl.setContentsMargins(0, 0, 0, 12)
        title = QLabel("TatkalMate AI")
        title.setObjectName("AppTitle")
        subtitle = QLabel("Tatkal preparation assistant")
        subtitle.setObjectName("AppSubtitle")
        sl.addWidget(title)
        sl.addWidget(subtitle)
        self.nav = QListWidget()
        self.nav.setObjectName("Nav")
        self.nav.setAccessibleName("Navigation")
        for item in NAV_ITEMS:
            QListWidgetItem(f"{item.glyph}   {item.label}", self.nav)
        sl.addWidget(self.nav, 1)
        from app.components.widgets import button
        self.quick_btn = button("Quick panel  (Ctrl+Q)", tooltip="Always-on-top countdown and copy helpers")
        self.quick_btn.clicked.connect(self.toggle_quick_panel)
        qb = QHBoxLayout()
        qb.setContentsMargins(12, 0, 12, 8)
        qb.addWidget(self.quick_btn)
        sl.addLayout(qb)
        ver = QLabel(f"v{__version__} · Asia/Kolkata")
        ver.setObjectName("AppSubtitle")
        sl.addWidget(ver)
        layout.addWidget(sidebar)

        self.stack = QStackedWidget()
        self.views = {
            "dashboard": DashboardView(ctx),
            "journey": JourneyView(ctx),
            "passengers": PassengersView(ctx),
            "preparation": PreparationView(ctx),
            "monitoring": MonitoringView(ctx, bridge),
            "notifications": NotificationsView(ctx),
            "browser": BrowserView(ctx),
            "settings": SettingsView(ctx),
            "diagnostics": DiagnosticsView(ctx, bridge),
        }
        for item in NAV_ITEMS:
            self.stack.addWidget(self.views[item.key])
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.status = QStatusBar()
        self.setStatusBar(self.status)
        self.status_label = QLabel()
        self.status.addPermanentWidget(self.status_label)

        self.nav.currentRowChanged.connect(self._on_nav)
        self.nav.setCurrentRow(index_of(ctx.config.config.ui.start_page))
        for i in range(min(9, len(NAV_ITEMS))):
            sc = QShortcut(QKeySequence(f"Ctrl+{i + 1}"), self)
            sc.activated.connect(lambda i=i: self.nav.setCurrentRow(i))

        self.quick_panel: QuickPanel | None = None
        QShortcut(QKeySequence("Ctrl+Q"), self).activated.connect(self.toggle_quick_panel)
        self.notifier = DesktopNotifier(bridge, self.show_and_raise, self.quit_app)
        ctx.notifications.add_sink(self.notifier)
        bridge.event.connect(self._on_event)
        self._update_status()

    def _on_nav(self, row: int) -> None:
        self.stack.setCurrentIndex(row)
        view = self.stack.currentWidget()
        if hasattr(view, "refresh"):
            view.refresh()

    def toggle_quick_panel(self) -> None:
        if self.quick_panel is None:
            self.quick_panel = QuickPanel(self.ctx)
            self.quick_panel.setStyleSheet(self.styleSheet())
            geo = self.screen().availableGeometry()
            self.quick_panel.move(geo.right() - 380, geo.top() + 40)
        if self.quick_panel.isVisible():
            self.quick_panel.hide()
        else:
            self.quick_panel.refresh()
            self.quick_panel.show()
            self.quick_panel.raise_()

    def _on_event(self, e: Event) -> None:
        if self.quick_panel is not None:
            self.quick_panel.handle_event(e)
        for view in self.views.values():
            handler = getattr(view, "handle_event", None)
            if handler:
                try:
                    handler(e)
                except Exception:
                    log.exception("view failed to handle event", extra={"event": e.type.value})
        if e.type is EventType.TATKAL_REMINDER and e.payload.get("minutes", 99) <= 5 \
                and (self.quick_panel is None or not self.quick_panel.isVisible()):
            self.toggle_quick_panel()  # pop the copy helpers up for the final minutes
        if e.type in (EventType.NOTIFICATION_CREATED, EventType.MONITOR_STATE_CHANGED,
                      EventType.NETWORK_CHANGED, EventType.BROWSER_STATE_CHANGED):
            self._update_status()
        if e.type in (EventType.JOURNEY_CHANGED, EventType.PASSENGERS_CHANGED, EventType.READINESS_CHANGED,
                      EventType.WALLET_CHANGED, EventType.CONFIG_CHANGED):
            current = self.stack.currentWidget()
            if current is not self.views["dashboard"] and hasattr(current, "refresh") \
                    and not hasattr(current, "handle_event"):
                current.refresh()

    def _update_status(self) -> None:
        unread = self.ctx.notifications.unread_count()
        snap = self.ctx.availability.snapshot()
        online = self.ctx.health.network_online
        net = {True: "online", False: "OFFLINE", None: "checking"}[online]
        self.status_label.setText(f"Monitor: {snap.state.value.lower()} · Network: {net} · "
                                  f"{unread} unread notification(s)")
        self.nav.item(index_of("notifications")).setText(
            f"◔   Notifications" + (f"  ({unread})" if unread else ""))

    def show_and_raise(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def quit_app(self) -> None:
        self._quitting = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:
        if (self.ctx.config.config.ui.minimize_to_tray and self.notifier.available and not self._quitting):
            event.ignore()
            self.hide()
            self.notifier.tray.showMessage("TatkalMate is still running", "Reminders and monitoring continue in the "
                                           "background. Use the tray icon to quit.")
            return
        self.notifier.hide()
        if self.quick_panel is not None:
            self.quick_panel.close()
        event.accept()
