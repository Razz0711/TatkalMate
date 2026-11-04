from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout

from app.components.formatting import ist
from app.components.widgets import Card, Page, button, fill_table, make_table, muted
from core.event_bus import Event, EventType
from models.notification import NotificationKind, Severity
from services.container import AppContext


class NotificationsView(Page):
    def __init__(self, ctx: AppContext):
        super().__init__("Notification center",
                         "Every alert TatkalMate has raised. Repeats of the same alert within the dedupe window "
                         "are suppressed.")
        self.ctx = ctx
        card = Card("Recent notifications")
        self.table = make_table(["Time (IST)", "Severity", "Type", "Title", "Details"])
        self.table.setMinimumHeight(380)
        card.body.addWidget(self.table)
        row = QHBoxLayout()
        self.read_btn = button("Mark all as read")
        self.clear_btn = button("Clear all", danger=True)
        self.test_btn = button("Send test notification")
        row.addWidget(self.read_btn)
        row.addWidget(self.clear_btn)
        row.addWidget(self.test_btn)
        row.addStretch(1)
        card.body.addLayout(row)
        self.summary = muted("")
        card.body.addWidget(self.summary)
        self.content.addWidget(card)
        self.content.addStretch(1)
        self.read_btn.clicked.connect(lambda: self.ctx.notifications.mark_read())
        self.clear_btn.clicked.connect(self.ctx.notifications.clear)
        self.test_btn.clicked.connect(self._test)
        self.refresh()

    def handle_event(self, e: Event) -> None:
        if e.type is EventType.NOTIFICATION_CREATED:
            self.refresh()

    def refresh(self) -> None:
        items = self.ctx.notifications.recent(300)
        fill_table(self.table, [[ist(n.created_at, "%d %b %H:%M:%S"), n.severity.value.title(),
                                 n.kind.value.replace("_", " ").title(), ("● " if not n.read else "") + n.title,
                                 n.body] for n in items])
        self.summary.setText(f"{self.ctx.notifications.unread_count()} unread · "
                             f"{self.ctx.notifications.suppressed_count} duplicates suppressed this session")

    def _test(self) -> None:
        n = self.ctx.notifications.notify(NotificationKind.SYSTEM, "Test notification",
                                          "Desktop and sound alerts are working.", Severity.INFO,
                                          dedupe_key="test", sound=True)
        if n is None:
            self.summary.setText("Test suppressed as a duplicate. Wait for the dedupe window to pass.")
