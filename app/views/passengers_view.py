from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QComboBox, QFormLayout, QHBoxLayout, QLineEdit, QMessageBox, QSpinBox

from app.components.widgets import Banner, Card, Page, button, fill_table, make_table, muted
from core.exceptions import ValidationError
from models.passenger import AGE_MAX, AGE_MIN, MAX_TATKAL_PASSENGERS, NAME_MAX, BerthPreference, FoodPreference, Gender, Passenger
from services.container import AppContext


class PassengersView(Page):
    def __init__(self, ctx: AppContext):
        super().__init__("Passenger profiles",
                         f"Stored only on this computer. Up to {MAX_TATKAL_PASSENGERS} passengers per Tatkal booking. "
                         "No passwords, OTPs, ID numbers or payment details are stored.")
        self.ctx = ctx
        self.editing_id: int | None = None
        self.banner = Banner()
        self.content.addWidget(self.banner)

        list_card = Card("Saved passengers")
        self.table = make_table(["Name", "Age", "Gender", "Berth", "Food", "In booking"])
        self.table.setMinimumHeight(200)
        list_card.body.addWidget(self.table)
        row = QHBoxLayout()
        self.edit_btn = button("Edit selected")
        self.delete_btn = button("Delete selected", danger=True)
        row.addWidget(self.edit_btn)
        row.addWidget(self.delete_btn)
        row.addStretch(1)
        list_card.body.addLayout(row)
        self.content.addWidget(list_card)

        form_card = Card("Add / edit passenger")
        form = QFormLayout()
        self.name = QLineEdit(placeholderText="As on ID proof")
        self.name.setMaxLength(NAME_MAX)
        self.age = QSpinBox()
        self.age.setRange(AGE_MIN, AGE_MAX)
        self.age.setValue(30)
        self.gender = QComboBox()
        for g in Gender:
            self.gender.addItem(g.label, g.value)
        self.berth = QComboBox()
        for b in BerthPreference:
            self.berth.addItem(b.label, b.value)
        self.food = QComboBox()
        for f in FoodPreference:
            self.food.addItem(f.label, f.value)
        self.include = QCheckBox("Include in this booking")
        self.include.setChecked(True)
        form.addRow("Name", self.name)
        form.addRow("Age", self.age)
        form.addRow("Gender", self.gender)
        form.addRow("Berth preference", self.berth)
        form.addRow("Food preference", self.food)
        form.addRow("", self.include)
        form_card.body.addLayout(form)
        form_card.body.addWidget(muted("Tatkal tickets have no senior-citizen concession; berth preference is a "
                                       "request, not a guarantee."))
        actions = QHBoxLayout()
        self.save_btn = button("Add passenger", primary=True)
        self.cancel_btn = button("Cancel edit")
        actions.addWidget(self.save_btn)
        actions.addWidget(self.cancel_btn)
        actions.addStretch(1)
        form_card.body.addLayout(actions)
        self.content.addWidget(form_card)
        self.content.addStretch(1)

        self.save_btn.clicked.connect(self._save)
        self.cancel_btn.clicked.connect(self._reset_form)
        self.edit_btn.clicked.connect(self._edit_selected)
        self.delete_btn.clicked.connect(self._delete_selected)
        self.table.doubleClicked.connect(lambda _i: self._edit_selected())
        self._reset_form()
        self.refresh()

    def refresh(self) -> None:
        ps = self.ctx.passengers.list()
        fill_table(self.table, [[p.name, str(p.age), p.gender.label, p.berth_preference.label,
                                 p.food_preference.label, "Yes" if p.include_in_booking else "No"] for p in ps],
                   [p.id for p in ps])
        warnings = self.ctx.passengers.warnings()
        if warnings and ps:
            self.banner.set(" ".join(warnings), "warn")

    def _selected_id(self) -> int | None:
        row = self.table.currentRow()
        if row < 0:
            return None
        return self.table.item(row, 0).data(Qt.ItemDataRole.UserRole)

    def _edit_selected(self) -> None:
        pid = self._selected_id()
        if pid is None:
            self.banner.set("Select a passenger first.", "warn")
            return
        p = next((x for x in self.ctx.passengers.list() if x.id == pid), None)
        if not p:
            return
        self.editing_id = p.id
        self.name.setText(p.name)
        self.age.setValue(p.age)
        self.gender.setCurrentIndex(self.gender.findData(p.gender.value))
        self.berth.setCurrentIndex(self.berth.findData(p.berth_preference.value))
        self.food.setCurrentIndex(self.food.findData(p.food_preference.value))
        self.include.setChecked(p.include_in_booking)
        self.save_btn.setText("Save changes")
        self.cancel_btn.setVisible(True)

    def _delete_selected(self) -> None:
        pid = self._selected_id()
        if pid is None:
            self.banner.set("Select a passenger first.", "warn")
            return
        if QMessageBox.question(self, "Delete passenger", "Delete this passenger profile?") \
                != QMessageBox.StandardButton.Yes:
            return
        self.ctx.passengers.delete(pid)
        if self.editing_id == pid:
            self._reset_form()
        self.banner.set("Passenger deleted.", "")
        self.refresh()

    def _reset_form(self) -> None:
        self.editing_id = None
        self.name.clear()
        self.age.setValue(30)
        self.gender.setCurrentIndex(0)
        self.berth.setCurrentIndex(0)
        self.food.setCurrentIndex(0)
        self.include.setChecked(True)
        self.save_btn.setText("Add passenger")
        self.cancel_btn.setVisible(False)

    def _save(self) -> None:
        p = Passenger(name=self.name.text(), age=self.age.value(), gender=Gender(self.gender.currentData()),
                      berth_preference=BerthPreference(self.berth.currentData()),
                      food_preference=FoodPreference(self.food.currentData()),
                      include_in_booking=self.include.isChecked(), id=self.editing_id)
        try:
            if self.editing_id is None:
                self.ctx.passengers.add(p)
                msg = "Passenger added."
            else:
                self.ctx.passengers.update(p)
                msg = "Passenger updated."
        except ValidationError as exc:
            self.banner.set(" ".join(exc.errors.values()), "bad")
            return
        self._reset_form()
        self.refresh()
        if not self.ctx.passengers.warnings():
            self.banner.set(msg, "ok")
