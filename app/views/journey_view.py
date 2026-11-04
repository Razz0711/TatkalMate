from __future__ import annotations

from datetime import date, timedelta

from PySide6.QtCore import QDate
from PySide6.QtWidgets import QComboBox, QDateEdit, QFormLayout, QHBoxLayout, QLineEdit, QSpinBox

from app.components.widgets import Banner, Card, KeyValueGrid, Page, button, muted, repolish
from core.exceptions import ValidationError
from core.scheduler import IST, compute_schedule
from models.journey import MAX_BOARDING_DAY_OFFSET, Journey, TatkalCategory, TravelClass
from services.container import AppContext


class JourneyView(Page):
    def __init__(self, ctx: AppContext):
        super().__init__("Journey configuration",
                         "Enter the journey you will book. This is used for the countdown and monitoring only; "
                         "you still enter it on IRCTC yourself.")
        self.ctx = ctx
        self.banner = Banner()
        self.content.addWidget(self.banner)

        card = Card("Journey details")
        form = QFormLayout()
        form.setSpacing(10)
        self.train_number = QLineEdit(placeholderText="e.g. 12951")
        self.train_number.setMaxLength(5)
        self.train_name = QLineEdit(placeholderText="Optional, e.g. Mumbai Rajdhani")
        self.from_station = QLineEdit(placeholderText="Boarding station code, e.g. ST")
        self.to_station = QLineEdit(placeholderText="Destination code, e.g. NDLS")
        for w in (self.from_station, self.to_station):
            w.setMaxLength(5)
        self.journey_date = QDateEdit(calendarPopup=True)
        self.journey_date.setDisplayFormat("ddd, dd MMM yyyy")
        self.travel_class = QComboBox()
        for c in TravelClass:
            if c.tatkal_eligible:
                self.travel_class.addItem(c.label, c.value)
        self.category = QComboBox()
        for c in TatkalCategory:
            self.category.addItem(c.label, c.value)
        self.offset = QSpinBox()
        self.offset.setRange(0, MAX_BOARDING_DAY_OFFSET)
        self.offset.setSuffix(" day(s) after origin")
        self.offset.setToolTip("0 if you board at the train's originating station. If the train left its origin "
                               "the day before it reaches your station, enter 1, and so on.")
        form.addRow("Train number", self.train_number)
        form.addRow("Train name", self.train_name)
        form.addRow("From (boarding)", self.from_station)
        form.addRow("To", self.to_station)
        form.addRow("Journey date (boarding)", self.journey_date)
        form.addRow("Class", self.travel_class)
        form.addRow("Quota", self.category)
        form.addRow("Boarding day", self.offset)
        card.body.addLayout(form)
        card.body.addWidget(muted("Tatkal opens one day before the train leaves its originating station, at 10:00 IST "
                                  "for AC classes and 11:00 IST for non-AC classes. If you board mid-route after "
                                  "midnight, set 'Boarding day' so the opening date is right."))
        actions = QHBoxLayout()
        self.save_btn = button("Save journey", primary=True)
        self.clear_btn = button("Clear", danger=True)
        actions.addWidget(self.save_btn)
        actions.addWidget(self.clear_btn)
        actions.addStretch(1)
        card.body.addLayout(actions)
        self.content.addWidget(card)

        preview = Card("Computed Tatkal schedule")
        self.preview = KeyValueGrid(["Category", "Origin departure", "Booking date", "Opens at"])
        preview.body.addWidget(self.preview)
        self.preview_banner = Banner()
        preview.body.addWidget(self.preview_banner)
        self.content.addWidget(preview)
        self.content.addStretch(1)

        self.fields = {"train_number": self.train_number, "from_station": self.from_station,
                       "to_station": self.to_station, "train_name": self.train_name}
        for w in (self.travel_class, self.category):
            w.currentIndexChanged.connect(self._update_preview)
        self.journey_date.dateChanged.connect(self._update_preview)
        self.offset.valueChanged.connect(self._update_preview)
        self.save_btn.clicked.connect(self._save)
        self.clear_btn.clicked.connect(self._clear)
        self.refresh()

    def refresh(self) -> None:
        j = self.ctx.journeys.active()
        today = self.ctx.scheduler.now_ist().date()
        if j:
            self.train_number.setText(j.train_number)
            self.train_name.setText(j.train_name)
            self.from_station.setText(j.from_station)
            self.to_station.setText(j.to_station)
            self.journey_date.setDate(QDate(j.journey_date.year, j.journey_date.month, j.journey_date.day))
            self.travel_class.setCurrentIndex(self.travel_class.findData(j.travel_class.value))
            self.category.setCurrentIndex(self.category.findData(j.category.value))
            self.offset.setValue(j.boarding_day_offset)
        else:
            d = today + timedelta(days=1)
            self.journey_date.setDate(QDate(d.year, d.month, d.day))
        self._update_preview()

    def _journey(self) -> Journey:
        qd = self.journey_date.date()
        return Journey(
            train_number=self.train_number.text(),
            train_name=self.train_name.text(),
            from_station=self.from_station.text(),
            to_station=self.to_station.text(),
            journey_date=date(qd.year(), qd.month(), qd.day()),
            travel_class=TravelClass(self.travel_class.currentData()),
            category=TatkalCategory(self.category.currentData()),
            boarding_day_offset=self.offset.value(),
        )

    def _update_preview(self) -> None:
        qd = self.journey_date.date()
        if self.travel_class.currentData() is None:
            return
        cls = TravelClass(self.travel_class.currentData())
        s = compute_schedule(cls, date(qd.year(), qd.month(), qd.day()), self.offset.value())
        self.preview.set("Category", s.window.label)
        self.preview.set("Origin departure", s.origin_departure_date.strftime("%a, %d %b %Y"))
        self.preview.set("Booking date", s.booking_date.strftime("%a, %d %b %Y"))
        self.preview.set("Opens at", s.opens_at.strftime("%H:%M IST"))
        now = self.ctx.clock.now_utc().astimezone(IST)
        if s.opens_at <= now:
            self.preview_banner.set("Booking for this date has already opened (or the date has passed).", "warn")
        else:
            self.preview_banner.set("")

    def _save(self) -> None:
        for w in self.fields.values():
            w.setProperty("invalid", False)
            w.setToolTip("")
            repolish(w)
        try:
            self.ctx.journeys.save(self._journey())
        except ValidationError as exc:
            for key, msg in exc.errors.items():
                w = self.fields.get(key)
                if w:
                    w.setProperty("invalid", True)
                    w.setToolTip(msg)
                    repolish(w)
            self.banner.set("Please fix: " + " ".join(exc.errors.values()), "bad")
            return
        self.refresh()
        self.banner.set("Journey saved.", "ok")

    def _clear(self) -> None:
        self.ctx.journeys.clear()
        for w in self.fields.values():
            w.clear()
        self.banner.set("Journey cleared.", "")
