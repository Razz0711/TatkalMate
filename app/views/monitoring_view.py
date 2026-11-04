from __future__ import annotations

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QCheckBox, QHBoxLayout

from app.components.formatting import ago, freshness_label, ist
from app.components.widgets import Banner, Card, KeyValueGrid, Page, Pill, button, fill_table, make_table, muted
from core.event_bus import Event, EventType
from core.exceptions import ProviderRateLimitedError
from models.availability import MonitorState
from services.container import AppContext

STATE_PILL = {
    MonitorState.IDLE: ("Idle", ""),
    MonitorState.RUNNING: ("Monitoring", "ok"),
    MonitorState.BACKING_OFF: ("Backing off", "warn"),
    MonitorState.STOPPED: ("Stopped", ""),
}


class MonitoringView(Page):
    def __init__(self, ctx: AppContext, bridge):
        super().__init__("Availability monitoring",
                         "Polls the configured provider at a safe interval and records every result. "
                         "TatkalMate never sends requests to the IRCTC website.")
        self.ctx = ctx
        self.bridge = bridge
        self.banner = Banner()  # standing warnings (simulated provider)
        self.content.addWidget(self.banner)
        self.action_banner = Banner()  # results of user actions
        self.content.addWidget(self.action_banner)

        pc = Card("Provider")
        self.provider_grid = KeyValueGrid(["Provider", "Type", "Status", "Poll interval", "Stale after"])
        pc.body.addWidget(self.provider_grid)
        self.content.addWidget(pc)

        mc = Card("Monitor")
        self.state_pill = Pill()
        mc.header.addWidget(self.state_pill)
        self.monitor_grid = KeyValueGrid(["Query", "Latest", "Freshness", "Last success", "Next check",
                                          "Failures in a row", "Last error", "Requests this session"])
        mc.body.addWidget(self.monitor_grid)
        row = QHBoxLayout()
        self.start_btn = button("Start monitoring", primary=True)
        self.stop_btn = button("Stop")
        self.check_btn = button("Check now", tooltip="One request, subject to the minimum interval.")
        row.addWidget(self.start_btn)
        row.addWidget(self.stop_btn)
        row.addWidget(self.check_btn)
        row.addStretch(1)
        mc.body.addLayout(row)
        self.content.addWidget(mc)

        hc = Card("History")
        self.changes_only = QCheckBox("Changes only")
        hc.header.addWidget(self.changes_only)
        self.table = make_table(["Fetched (IST)", "Status", "Freshness at fetch", "Source time", "Source", "Changed"])
        self.table.setMinimumHeight(260)
        hc.body.addWidget(self.table)
        self.content.addWidget(hc)
        self.content.addStretch(1)

        self.start_btn.clicked.connect(self._start)
        self.stop_btn.clicked.connect(self._stop)
        self.check_btn.clicked.connect(self._check)
        self.changes_only.toggled.connect(lambda _c: self.refresh())
        self.clock_timer = QTimer(self)
        self.clock_timer.setInterval(1000)
        self.clock_timer.timeout.connect(self._refresh_times)
        self.clock_timer.start()
        self.refresh()

    def handle_event(self, e: Event) -> None:
        if e.type in (EventType.MONITOR_STATE_CHANGED, EventType.AVAILABILITY_RESULT, EventType.JOURNEY_CHANGED,
                      EventType.HEALTH_UPDATED, EventType.CONFIG_CHANGED, EventType.AVAILABILITY_STALE):
            self.refresh()

    def _refresh_times(self) -> None:
        snap = self.ctx.availability.snapshot()
        now = self.ctx.clock.now_utc()
        self.monitor_grid.set("Last success", f"{ist(snap.last_success_at, '%H:%M:%S')} ({ago(snap.last_success_at, now)})"
                              if snap.last_success_at else "—")
        if snap.next_attempt_at:
            secs = max(0, int((snap.next_attempt_at - now).total_seconds()))
            self.monitor_grid.set("Next check", f"in {secs // 60}m {secs % 60:02d}s")
        else:
            self.monitor_grid.set("Next check", "—")

    def refresh(self) -> None:
        av = self.ctx.availability
        info = av.provider.info
        self.provider_grid.set("Provider", info.name)
        self.provider_grid.set("Type", "Real data source" if info.is_real_source else
                               "SIMULATED: not connected to any railway data")
        self.provider_grid.set("Status", f"{self.ctx.health.provider_health.value}: {self.ctx.health.provider_message}"
                               if self.ctx.health.provider_health else "Checking…")
        self.provider_grid.set("Poll interval", f"{av.effective_poll_interval:.0f} s (minimum allowed gap "
                               f"{av.min_request_gap:.0f} s)")
        self.provider_grid.set("Stale after", f"{av.config.stale_after_seconds:.0f} s")
        if not info.is_real_source:
            self.banner.set("No authorized real-time availability source is connected. Results below are simulated "
                            "for testing and must not be used to decide whether seats exist.", "warn")
        else:
            self.banner.set("")

        snap = av.snapshot()
        text, tone = STATE_PILL[snap.state]
        self.state_pill.set(text, tone)
        q = self.ctx.journeys.availability_query()
        self.start_btn.setEnabled(q is not None and snap.state not in (MonitorState.RUNNING, MonitorState.BACKING_OFF))
        self.stop_btn.setEnabled(snap.state in (MonitorState.RUNNING, MonitorState.BACKING_OFF))
        self.check_btn.setEnabled(q is not None)
        self.monitor_grid.set("Query", f"{q.train_number} · {q.journey_date:%d %b %Y} · {q.travel_class.value} · "
                              f"{q.category.label}" if q else "Configure a journey first")
        now = self.ctx.clock.now_utc()
        latest = av.latest(q) if q else None
        self.monitor_grid.set("Latest", latest.describe() if latest else "—")
        if latest:
            self.monitor_grid.set("Freshness", freshness_label(latest, now, av.config.stale_after_seconds)[0])
        else:
            self.monitor_grid.set("Freshness", "—")
        self.monitor_grid.set("Failures in a row", str(snap.consecutive_failures))
        self.monitor_grid.set("Last error", snap.last_error or "—")
        self.monitor_grid.set("Requests this session", str(snap.requests_made))
        self._refresh_times()
        if q:
            rows = av.history(q, limit=200, changes_only=self.changes_only.isChecked())
            stale_after = av.config.stale_after_seconds
            fill_table(self.table, [[
                ist(r.fetched_at, "%d %b %H:%M:%S"), r.describe(),
                freshness_label(r, r.fetched_at, stale_after)[0],
                ist(r.source_timestamp, "%H:%M:%S") if r.source_timestamp else "not provided",
                r.source, "Yes" if changed else ""] for r, changed in rows])
        else:
            fill_table(self.table, [])

    def _start(self) -> None:
        q = self.ctx.journeys.availability_query()
        if q:
            self.ctx.availability.start(q)

    def _stop(self) -> None:
        self.ctx.availability.stop()

    def _check(self) -> None:
        q = self.ctx.journeys.availability_query()
        if not q:
            return
        self.check_btn.setEnabled(False)
        fut = self.ctx.runtime.submit(self.ctx.availability.check_now(q))

        def ok(_r) -> None:
            self.check_btn.setEnabled(True)
            self.action_banner.set("")
            self.refresh()

        def err(exc: BaseException) -> None:
            self.check_btn.setEnabled(True)
            if isinstance(exc, ProviderRateLimitedError):
                self.action_banner.set(f"Too soon: the next request is allowed in {exc.retry_after:.0f} s. "
                                "This limit protects the provider and is not configurable below 60 s.", "warn")
            else:
                self.action_banner.set(f"Check failed: {exc}", "bad")
            self.refresh()

        self.bridge.on_done(fut, ok, err)
