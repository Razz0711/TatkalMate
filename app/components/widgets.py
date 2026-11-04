"""Reusable widgets."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QAbstractItemView,
    QHeaderView,
)


def repolish(w: QWidget) -> None:
    w.style().unpolish(w)
    w.style().polish(w)


class Card(QFrame):
    def __init__(self, title: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("Card")
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(18, 14, 18, 16)
        self.body.setSpacing(10)
        if title:
            header = QHBoxLayout()
            self.title_label = QLabel(title.upper())
            self.title_label.setObjectName("CardTitle")
            header.addWidget(self.title_label)
            header.addStretch(1)
            self.header = header
            self.body.addLayout(header)


class Pill(QLabel):
    def __init__(self, text: str = "", tone: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("Pill")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.set(text, tone)

    def set(self, text: str, tone: str = "") -> None:
        self.setText(text)
        self.setProperty("tone", tone)
        self.setAccessibleName(text)
        repolish(self)


class Banner(QLabel):
    def __init__(self, text: str = "", tone: str = "", parent: QWidget | None = None):
        super().__init__(text, parent)
        self.setObjectName("Banner")
        self.setWordWrap(True)
        self.set(text, tone)

    def set(self, text: str, tone: str = "") -> None:
        self.setText(text)
        self.setProperty("tone", tone)
        self.setVisible(bool(text))
        repolish(self)


class KeyValueGrid(QWidget):
    def __init__(self, keys: list[str], parent: QWidget | None = None):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)
        self.values: dict[str, QWidget] = {}
        for row, key in enumerate(keys):
            k = QLabel(key)
            k.setObjectName("KeyLabel")
            v = QLabel("—")
            v.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            v.setWordWrap(True)
            grid.addWidget(k, row, 0, Qt.AlignmentFlag.AlignTop)
            grid.addWidget(v, row, 1)
            self.values[key] = v
        grid.setColumnStretch(1, 1)

    def set(self, key: str, value: str) -> None:
        w = self.values[key]
        if isinstance(w, QLabel):
            w.setText(value or "—")

    def set_widget(self, key: str, widget: QWidget) -> None:
        old = self.values[key]
        layout: QGridLayout = self.layout()  # type: ignore[assignment]
        idx = layout.indexOf(old)
        row, col, _, _ = layout.getItemPosition(idx)
        layout.removeWidget(old)
        old.deleteLater()
        layout.addWidget(widget, row, col, Qt.AlignmentFlag.AlignLeft)
        self.values[key] = widget


class Page(QScrollArea):
    """Scrollable page with a title and subtitle."""

    def __init__(self, title: str, subtitle: str = "", parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        inner = QWidget()
        inner.setObjectName("Page")
        self.content = QVBoxLayout(inner)
        self.content.setContentsMargins(28, 22, 28, 28)
        self.content.setSpacing(16)
        t = QLabel(title)
        t.setObjectName("PageTitle")
        self.content.addWidget(t)
        if subtitle:
            s = QLabel(subtitle)
            s.setObjectName("PageSubtitle")
            s.setWordWrap(True)
            self.content.addWidget(s)
        self.setWidget(inner)

    def refresh(self) -> None:  # overridden by views
        pass


def make_table(headers: list[str]) -> QTableWidget:
    t = QTableWidget(0, len(headers))
    t.setHorizontalHeaderLabels(headers)
    t.verticalHeader().setVisible(False)
    t.setAlternatingRowColors(True)
    t.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    t.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    t.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    t.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
    t.setWordWrap(False)
    return t


def fill_table(table: QTableWidget, rows: list[list[str]], data: list[object] | None = None) -> None:
    table.setRowCount(len(rows))
    for r, row in enumerate(rows):
        for c, value in enumerate(row):
            item = QTableWidgetItem(value)
            if data is not None and c == 0:
                item.setData(Qt.ItemDataRole.UserRole, data[r])
            table.setItem(r, c, item)


def button(text: str, primary: bool = False, danger: bool = False, tooltip: str = "") -> QPushButton:
    b = QPushButton(text)
    if primary:
        b.setProperty("primary", True)
    if danger:
        b.setProperty("danger", True)
    if tooltip:
        b.setToolTip(tooltip)
    b.setAccessibleName(text)
    b.setCursor(Qt.CursorShape.PointingHandCursor)
    return b


def muted(text: str, wrap: bool = True) -> QLabel:
    l = QLabel(text)
    l.setObjectName("Muted")
    l.setWordWrap(wrap)
    return l
