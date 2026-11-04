from __future__ import annotations

import copy

from PySide6.QtWidgets import QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout, QLineEdit, QSpinBox

from app.components.widgets import Banner, Card, Page, button, muted
from core.config import HARD_MIN_POLL_SECONDS
from core.exceptions import ConfigError
from services.container import AppContext


class SettingsView(Page):
    def __init__(self, ctx: AppContext):
        super().__init__("Settings", "Saved on this computer. Secrets such as provider API keys go in a .env file, "
                                     "never here.")
        self.ctx = ctx
        self.banner = Banner()
        self.content.addWidget(self.banner)

        mon = Card("Availability monitoring")
        f = QFormLayout()
        self.provider = QComboBox()
        self.provider.addItem("Mock provider (simulated data)", "mock")
        self.provider.addItem("Authorized provider (configure in .env)", "authorized")
        self.poll = QSpinBox()
        self.poll.setRange(int(HARD_MIN_POLL_SECONDS), 3600)
        self.poll.setSuffix(" s")
        self.stale = QSpinBox()
        self.stale.setRange(60, 7200)
        self.stale.setSuffix(" s")
        self.timeout = QSpinBox()
        self.timeout.setRange(3, 60)
        self.timeout.setSuffix(" s")
        f.addRow("Data provider", self.provider)
        f.addRow("Poll interval", self.poll)
        f.addRow("Mark data stale after", self.stale)
        f.addRow("Request timeout", self.timeout)
        mon.body.addLayout(f)
        mon.body.addWidget(muted(f"The poll interval cannot go below {HARD_MIN_POLL_SECONDS:.0f} s, or below what the "
                                 "provider allows. Switching provider takes effect when monitoring is stopped."))
        self.content.addWidget(mon)

        notif = Card("Notifications")
        f = QFormLayout()
        self.desktop = QCheckBox("Windows desktop notifications")
        self.sound = QCheckBox("Sound alerts for important events")
        self.reminders = QCheckBox("Tatkal opening reminders")
        self.reminder_minutes = QLineEdit(placeholderText="e.g. 30, 10, 2")
        self.refresh_min = QSpinBox()
        self.refresh_min.setRange(0, 60)
        self.refresh_min.setSuffix(" min before (0 = off)")
        self.dedupe = QSpinBox()
        self.dedupe.setRange(10, 3600)
        self.dedupe.setSuffix(" s")
        f.addRow("", self.desktop)
        f.addRow("", self.sound)
        f.addRow("", self.reminders)
        f.addRow("Remind me (minutes before)", self.reminder_minutes)
        f.addRow("'Refresh IRCTC' reminder", self.refresh_min)
        f.addRow("Suppress duplicates for", self.dedupe)
        notif.body.addLayout(f)
        self.content.addWidget(notif)

        br = Card("Browser")
        f = QFormLayout()
        path_row = QHBoxLayout()
        self.brave_path = QLineEdit(placeholderText="Auto-detect")
        browse = button("Browse…")
        browse.clicked.connect(self._browse)
        path_row.addWidget(self.brave_path, 1)
        path_row.addWidget(browse)
        self.irctc_url = QLineEdit()
        self.auto_launch = QSpinBox()
        self.auto_launch.setRange(0, 240)
        self.auto_launch.setSuffix(" min before opening (0 = off)")
        self.focus_open = QCheckBox("Bring Brave to the front when Tatkal opens")
        f.addRow("Brave executable", path_row)
        f.addRow("IRCTC URL", self.irctc_url)
        f.addRow("Auto-open Brave", self.auto_launch)
        f.addRow("", self.focus_open)
        br.body.addLayout(f)
        self.content.addWidget(br)

        ts = Card("IST time sync")
        f = QFormLayout()
        self.ntp_enabled = QCheckBox("Correct the countdown using internet time (NTP)")
        self.ntp_servers = QLineEdit()
        f.addRow("", self.ntp_enabled)
        f.addRow("NTP servers", self.ntp_servers)
        ts.body.addLayout(f)
        self.content.addWidget(ts)

        net = Card("Network check")
        f = QFormLayout()
        self.probe_host = QLineEdit()
        self.probe_port = QSpinBox()
        self.probe_port.setRange(1, 65535)
        f.addRow("Probe host", self.probe_host)
        f.addRow("Probe port", self.probe_port)
        net.body.addLayout(f)
        net.body.addWidget(muted("A TCP connection to this neutral host decides whether you're online. It never "
                                 "contacts IRCTC."))
        self.content.addWidget(net)

        row = QHBoxLayout()
        self.save_btn = button("Save settings", primary=True)
        self.reset_btn = button("Revert")
        row.addWidget(self.save_btn)
        row.addWidget(self.reset_btn)
        row.addStretch(1)
        self.content.addLayout(row)
        self.content.addStretch(1)
        self.save_btn.clicked.connect(self._save)
        self.reset_btn.clicked.connect(self.refresh)
        self.refresh()

    def refresh(self) -> None:
        c = self.ctx.config.config
        self.provider.setCurrentIndex(self.provider.findData(c.monitoring.provider))
        self.poll.setValue(int(c.monitoring.poll_interval_seconds))
        self.stale.setValue(int(c.monitoring.stale_after_seconds))
        self.timeout.setValue(int(c.monitoring.request_timeout_seconds))
        self.desktop.setChecked(c.notifications.desktop_enabled)
        self.sound.setChecked(c.notifications.sound_enabled)
        self.reminders.setChecked(c.notifications.opening_reminders_enabled)
        self.reminder_minutes.setText(", ".join(str(m) for m in c.notifications.reminder_minutes))
        self.dedupe.setValue(int(c.notifications.dedupe_window_seconds))
        self.refresh_min.setValue(c.notifications.refresh_reminder_minutes)
        self.auto_launch.setValue(c.browser.auto_launch_minutes_before)
        self.focus_open.setChecked(c.browser.focus_at_opening)
        self.ntp_enabled.setChecked(c.time_sync.enabled)
        self.ntp_servers.setText(", ".join(c.time_sync.servers))
        self.brave_path.setText(c.browser.brave_path_override)
        self.irctc_url.setText(c.browser.irctc_url)
        self.probe_host.setText(c.network.probe_host)
        self.probe_port.setValue(c.network.probe_port)

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Locate brave.exe", "", "Brave (brave.exe brave);;All files (*)")
        if path:
            self.brave_path.setText(path)

    def _save(self) -> None:
        c = copy.deepcopy(self.ctx.config.config)
        try:
            minutes = [int(x) for x in self.reminder_minutes.text().replace(";", ",").split(",") if x.strip()]
        except ValueError:
            self.banner.set("Reminder minutes must be whole numbers separated by commas.", "bad")
            return
        new_provider = self.provider.currentData()
        note = ""
        if new_provider != c.monitoring.provider and self.ctx.availability.is_monitoring:
            note = " Stop monitoring for the provider change to take effect."
        c.monitoring.provider = new_provider
        c.monitoring.poll_interval_seconds = float(self.poll.value())
        c.monitoring.stale_after_seconds = float(self.stale.value())
        c.monitoring.request_timeout_seconds = float(self.timeout.value())
        c.notifications.desktop_enabled = self.desktop.isChecked()
        c.notifications.sound_enabled = self.sound.isChecked()
        c.notifications.opening_reminders_enabled = self.reminders.isChecked()
        c.notifications.reminder_minutes = minutes
        c.notifications.dedupe_window_seconds = float(self.dedupe.value())
        c.notifications.refresh_reminder_minutes = self.refresh_min.value()
        c.browser.auto_launch_minutes_before = self.auto_launch.value()
        c.browser.focus_at_opening = self.focus_open.isChecked()
        c.time_sync.enabled = self.ntp_enabled.isChecked()
        c.time_sync.servers = [x.strip() for x in self.ntp_servers.text().split(",") if x.strip()]
        c.browser.brave_path_override = self.brave_path.text().strip()
        c.browser.irctc_url = self.irctc_url.text().strip()
        c.network.probe_host = self.probe_host.text().strip() or "1.1.1.1"
        c.network.probe_port = self.probe_port.value()
        try:
            self.ctx.config.save(c)
        except ConfigError as exc:
            self.banner.set(str(exc), "bad")
            return
        self.refresh()
        self.banner.set("Settings saved." + note, "ok")
