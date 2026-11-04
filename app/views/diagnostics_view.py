from __future__ import annotations

import platform
import sys

from PySide6 import __version__ as pyside_version
from PySide6.QtCore import QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QHBoxLayout, QPlainTextEdit

from app.components.formatting import ist
from app.components.widgets import Card, KeyValueGrid, Page, Pill, button, fill_table, make_table
from core.event_bus import Event, EventType
from core.logging_config import LOG_FILE_NAME, tail_log
from core.recovery import list_crash_reports
from core.version import version_string
from services.container import AppContext
from services.health_service import CheckStatus

TONE = {CheckStatus.OK: "ok", CheckStatus.WARN: "warn", CheckStatus.FAIL: "bad"}


class DiagnosticsView(Page):
    def __init__(self, ctx: AppContext, bridge):
        super().__init__("Logs and diagnostics", "Health checks, version information and the application log. "
                                                 "Personal details are masked in logs.")
        self.ctx = ctx
        self.bridge = bridge

        hc = Card("Health checks")
        self.overall = Pill()
        hc.header.addWidget(self.overall)
        self.table = make_table(["Check", "Status", "Detail"])
        self.table.setMinimumHeight(280)
        from PySide6.QtWidgets import QHeaderView
        hdr = self.table.horizontalHeader()
        hdr.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        hdr.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        hc.body.addWidget(self.table)
        row = QHBoxLayout()
        self.run_btn = button("Run checks now")
        row.addWidget(self.run_btn)
        row.addStretch(1)
        hc.body.addLayout(row)
        self.content.addWidget(hc)

        ver = Card("About")
        self.about = KeyValueGrid(["Application", "Python", "Qt binding", "OS", "Data folder", "Crash reports"])
        ver.body.addWidget(self.about)
        self.content.addWidget(ver)

        lc = Card("Application log")
        self.log_view = QPlainTextEdit()
        self.log_view.setObjectName("LogView")
        self.log_view.setReadOnly(True)
        self.log_view.setMinimumHeight(260)
        lc.body.addWidget(self.log_view)
        row = QHBoxLayout()
        self.reload_btn = button("Reload log")
        self.folder_btn = button("Open logs folder")
        row.addWidget(self.reload_btn)
        row.addWidget(self.folder_btn)
        row.addStretch(1)
        lc.body.addLayout(row)
        self.content.addWidget(lc)
        self.content.addStretch(1)

        self.run_btn.clicked.connect(self._run)
        self.reload_btn.clicked.connect(self._load_log)
        self.folder_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl.fromLocalFile(str(ctx.paths.log_dir))))
        self.refresh()

    def handle_event(self, e: Event) -> None:
        if e.type is EventType.HEALTH_UPDATED:
            self._show_report()

    def refresh(self) -> None:
        crashes = list_crash_reports(self.ctx.paths.crash_dir)
        self.about.set("Application", version_string())
        self.about.set("Python", sys.version.split()[0])
        self.about.set("Qt binding", f"PySide6 {pyside_version}")
        self.about.set("OS", f"{platform.system()} {platform.release()} ({platform.machine()})")
        self.about.set("Data folder", str(self.ctx.paths.root))
        self.about.set("Crash reports", f"{len(crashes)} (latest: {crashes[0].name})" if crashes else "None")
        self._show_report()
        self._load_log()

    def _show_report(self) -> None:
        report = self.ctx.health.latest
        if not report:
            self.overall.set("Not run yet", "")
            return
        self.overall.set(f"{report.overall.value} · {ist(report.generated_at, '%H:%M:%S')}", TONE[report.overall])
        fill_table(self.table, [[c.name, c.status.value, c.detail] for c in report.checks])

    def _run(self) -> None:
        if not self.ctx.runtime.is_running:
            return
        self.run_btn.setEnabled(False)
        fut = self.ctx.runtime.submit(self.ctx.health.run_checks())
        self.bridge.on_done(fut, lambda _r: (self.run_btn.setEnabled(True), self._show_report()),
                            lambda _e: self.run_btn.setEnabled(True))

    def _load_log(self) -> None:
        lines = tail_log(self.ctx.paths.log_dir / LOG_FILE_NAME, 400)
        self.log_view.setPlainText("\n".join(lines))
        sb = self.log_view.verticalScrollBar()
        sb.setValue(sb.maximum())
