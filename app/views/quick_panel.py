"""Always-on-top quick panel: countdown plus one-click copy of journey and
passenger fields, for the final minutes before Tatkal opens."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from app.components.widgets import Card, button, muted
from core.event_bus import Event, EventType
from services.container import AppContext


def journey_fields(ctx: AppContext) -> list[tuple[str, str]]:
    j = ctx.journeys.active()
    if not j:
        return []
    return [
        ("Train", j.train_number),
        ("From", j.from_station),
        ("To", j.to_station),
        ("Date", j.journey_date.strftime("%d/%m/%Y")),
        ("Class", j.travel_class.value),
        ("Quota", "TATKAL" if j.category.value == "TQ" else "PREMIUM TATKAL"),
    ]


class QuickPanel(QWidget):
    def __init__(self, ctx: AppContext):
        super().__init__(None, Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint)
        self.ctx = ctx
        self.setObjectName("Page")
        self.setWindowTitle("TatkalMate quick panel")
        self.setMinimumWidth(340)
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        self.countdown = QLabel("--:--:--.---")
        self.countdown.setObjectName("Countdown")
        self.countdown.setStyleSheet("font-size: 22pt;")
        root.addWidget(self.countdown)
        self.opens = muted("")
        root.addWidget(self.opens)

        self.journey_card = Card("Journey · click to copy")
        self.journey_grid = QGridLayout()
        self.journey_card.body.addLayout(self.journey_grid)
        root.addWidget(self.journey_card)

        self.pax_card = Card("Passengers · click to copy")
        self.pax_grid = QGridLayout()
        self.pax_card.body.addLayout(self.pax_grid)
        root.addWidget(self.pax_card)

        row = QHBoxLayout()
        self.front_btn = button("Brave to front", primary=True)
        self.open_btn = button("Open IRCTC")
        row.addWidget(self.front_btn)
        row.addWidget(self.open_btn)
        root.addLayout(row)
        self.status = muted("")
        root.addWidget(self.status)

        self.front_btn.clicked.connect(self._front)
        self.open_btn.clicked.connect(self._open)
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.refresh()

    def handle_event(self, e: Event) -> None:
        if e.type in (EventType.JOURNEY_CHANGED, EventType.PASSENGERS_CHANGED):
            self.refresh()

    @staticmethod
    def _clear(grid: QGridLayout) -> None:
        while grid.count():
            w = grid.takeAt(0).widget()
            if w:
                w.hide()
                w.setParent(None)
                w.deleteLater()

    def _copy_button(self, text: str, label: str) -> QPushButton:
        b = button(text, tooltip=f"Copy {label}")
        b.setStyleSheet("text-align: left; padding: 5px 10px;")
        b.clicked.connect(lambda _=False, t=text, l=label: self.copy(t, l))
        return b

    def copy(self, text: str, label: str) -> None:
        QGuiApplication.clipboard().setText(text)
        self.status.setText(f"Copied {label}")

    def refresh(self) -> None:
        self._clear(self.journey_grid)
        fields = journey_fields(self.ctx)
        if not fields:
            self.journey_grid.addWidget(muted("No journey saved."), 0, 0)
        for r, (k, v) in enumerate(fields):
            self.journey_grid.addWidget(muted(k, wrap=False), r, 0)
            self.journey_grid.addWidget(self._copy_button(v, k.lower()), r, 1)
        self._clear(self.pax_grid)
        pax = self.ctx.passengers.booking_passengers()
        if not pax:
            self.pax_grid.addWidget(muted("No passengers selected."), 0, 0)
        for r, p in enumerate(pax):
            self.pax_grid.addWidget(self._copy_button(p.name, f"passenger {r + 1} name"), r, 0)
            self.pax_grid.addWidget(self._copy_button(str(p.age), f"passenger {r + 1} age"), r, 1)
            self.pax_grid.addWidget(muted(f"{p.gender.label} · {p.berth_preference.label}", wrap=False), r, 2)
        self.pax_grid.setColumnStretch(0, 1)
        s = self.ctx.scheduler.schedule
        self.opens.setText(f"{s.window.label} opens {s.opens_at:%d %b, %H:%M} IST" if s else "")

    def _tick(self) -> None:
        cd = self.ctx.scheduler.countdown()
        self.countdown.setText(cd.format() if cd else "--:--:--.---")

    def _front(self) -> None:
        ok, msg = self.ctx.brave.bring_to_foreground()
        self.status.setText(msg)

    def _open(self) -> None:
        try:
            self.ctx.brave.launch(self.ctx.config.config.browser.irctc_url)
            self.status.setText("Opened IRCTC in Brave.")
        except Exception as exc:
            self.status.setText(str(exc))
