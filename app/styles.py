"""Dark navy / charcoal theme."""

from __future__ import annotations

import sys

COLORS = {
    "bg": "#0B1220",
    "sidebar": "#0E1628",
    "surface": "#121B2E",
    "surface_alt": "#17223A",
    "border": "#22304B",
    "text": "#E6EBF5",
    "muted": "#8D9AB5",
    "accent": "#4C8DFF",
    "accent_hover": "#6AA0FF",
    "ok": "#2FBF71",
    "ok_bg": "#123326",
    "warn": "#E8B04B",
    "warn_bg": "#3A2E14",
    "bad": "#EF6461",
    "bad_bg": "#3B1A1D",
    "info_bg": "#16294A",
}

UI_FONT = "Segoe UI" if sys.platform.startswith("win") else "Inter, Segoe UI, Noto Sans, sans-serif"
MONO_FONT = "Cascadia Mono" if sys.platform.startswith("win") else "JetBrains Mono, DejaVu Sans Mono, monospace"


def stylesheet() -> str:
    c = COLORS
    return f"""
* {{ font-family: {UI_FONT}; font-size: 10pt; color: {c['text']}; }}
QMainWindow, QWidget#Page, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {c['bg']}; }}
QWidget#Sidebar {{ background: {c['sidebar']}; border-right: 1px solid {c['border']}; }}
QLabel#AppTitle {{ font-size: 14pt; font-weight: 600; padding: 18px 16px 2px 16px; }}
QLabel#AppSubtitle {{ color: {c['muted']}; padding: 0 16px 14px 16px; font-size: 9pt; }}
QListWidget#Nav {{ background: transparent; border: none; outline: 0; padding: 4px 8px; }}
QListWidget#Nav::item {{ padding: 9px 12px; border-radius: 6px; margin: 1px 0; color: {c['muted']}; }}
QListWidget#Nav::item:hover {{ background: {c['surface']}; color: {c['text']}; }}
QListWidget#Nav::item:selected {{ background: {c['surface_alt']}; color: {c['text']}; border-left: 3px solid {c['accent']}; }}
QLabel#PageTitle {{ font-size: 16pt; font-weight: 600; }}
QLabel#PageSubtitle {{ color: {c['muted']}; }}
QFrame#Card {{ background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px; }}
QLabel#CardTitle {{ color: {c['muted']}; font-size: 9pt; font-weight: 600; letter-spacing: 1px; text-transform: uppercase; }}
QLabel#Muted {{ color: {c['muted']}; }}
QLabel#KeyLabel {{ color: {c['muted']}; }}
QLabel#Countdown {{ font-family: {MONO_FONT}; font-size: 34pt; font-weight: 600; letter-spacing: 1px; }}
QLabel#BigStatus {{ font-size: 18pt; font-weight: 600; }}
QLabel#Banner {{ border-radius: 6px; padding: 8px 10px; background: {c['info_bg']}; }}
QLabel#Banner[tone="warn"] {{ background: {c['warn_bg']}; color: {c['warn']}; }}
QLabel#Banner[tone="bad"] {{ background: {c['bad_bg']}; color: {c['bad']}; }}
QLabel#Banner[tone="ok"] {{ background: {c['ok_bg']}; color: {c['ok']}; }}
QLabel#Pill {{ border-radius: 9px; padding: 2px 10px; font-size: 9pt; font-weight: 600;
              background: {c['surface_alt']}; color: {c['muted']}; }}
QLabel#Pill[tone="ok"] {{ background: {c['ok_bg']}; color: {c['ok']}; }}
QLabel#Pill[tone="warn"] {{ background: {c['warn_bg']}; color: {c['warn']}; }}
QLabel#Pill[tone="bad"] {{ background: {c['bad_bg']}; color: {c['bad']}; }}
QLabel#Pill[tone="info"] {{ background: {c['info_bg']}; color: {c['accent_hover']}; }}
QPushButton {{ background: {c['surface_alt']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 7px 14px; }}
QPushButton:hover {{ border-color: {c['accent']}; }}
QPushButton:focus {{ border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['muted']}; background: {c['surface']}; }}
QPushButton[primary="true"] {{ background: {c['accent']}; border-color: {c['accent']}; color: white; font-weight: 600; }}
QPushButton[primary="true"]:hover {{ background: {c['accent_hover']}; }}
QPushButton[danger="true"] {{ color: {c['bad']}; }}
QLineEdit, QSpinBox, QDoubleSpinBox, QDateEdit, QComboBox, QPlainTextEdit, QTextBrowser {{
    background: {c['bg']}; border: 1px solid {c['border']}; border-radius: 6px; padding: 5px 8px;
    selection-background-color: {c['accent']}; }}
QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QComboBox:focus {{ border-color: {c['accent']}; }}
QLineEdit[invalid="true"] {{ border-color: {c['bad']}; }}
QComboBox QAbstractItemView {{ background: {c['surface']}; border: 1px solid {c['border']}; selection-background-color: {c['surface_alt']}; }}
QPlainTextEdit#LogView {{ font-family: {MONO_FONT}; font-size: 9pt; }}
QTableWidget {{ background: {c['surface']}; alternate-background-color: {c['surface_alt']}; border: 1px solid {c['border']};
                border-radius: 6px; gridline-color: {c['border']}; }}
QHeaderView::section {{ background: {c['surface_alt']}; color: {c['muted']}; border: none; padding: 6px; font-weight: 600; }}
QTableWidget::item:selected {{ background: {c['info_bg']}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; }}
QScrollBar:vertical {{ background: transparent; width: 10px; }}
QScrollBar::handle:vertical {{ background: {c['border']}; border-radius: 5px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QStatusBar {{ background: {c['sidebar']}; color: {c['muted']}; border-top: 1px solid {c['border']}; }}
QToolTip {{ background: {c['surface_alt']}; color: {c['text']}; border: 1px solid {c['border']}; }}
QGroupBox {{ border: 1px solid {c['border']}; border-radius: 8px; margin-top: 14px; padding-top: 10px; }}
QGroupBox::title {{ subcontrol-origin: margin; left: 10px; color: {c['muted']}; }}
"""
