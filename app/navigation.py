"""Sidebar navigation definition."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NavItem:
    key: str
    label: str
    glyph: str


NAV_ITEMS: list[NavItem] = [
    NavItem("dashboard", "Dashboard", "◧"),
    NavItem("journey", "Journey", "⇄"),
    NavItem("passengers", "Passengers", "☺"),
    NavItem("preparation", "Preparation", "✓"),
    NavItem("monitoring", "Availability", "◉"),
    NavItem("notifications", "Notifications", "◔"),
    NavItem("browser", "Browser", "◎"),
    NavItem("settings", "Settings", "⚙"),
    NavItem("diagnostics", "Logs & diagnostics", "≡"),
]


def index_of(key: str) -> int:
    for i, item in enumerate(NAV_ITEMS):
        if item.key == key:
            return i
    return 0
