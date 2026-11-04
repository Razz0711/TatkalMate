from __future__ import annotations

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import QGridLayout, QLabel, QWidget

from app.components.formatting import CHECK_SOURCE, CHECK_STATE, STATUS_TONE, ago, freshness_label, ist
from app.components.widgets import Banner, Card, KeyValueGrid, Page, Pill, fill_table, make_table, muted
from core.event_bus import Event, EventType
from core.scheduler import Phase
from models.availability import Freshness, MonitorState
from services.brave_service import BrowserState
from services.container import AppContext
from services.readiness_service import LOGIN_COMPLETED

PHASE_TEXT = {
    Phase.NO_JOURNEY: "No journey configured",
    Phase.BEFORE_BOOKING_DAY: "Waiting for booking day",
    Phase.BOOKING_DAY_PRE_OPEN: "Booking opens today",
    Phase.OPEN: "Tatkal booking is OPEN",
    Phase.JOURNEY_PASSED: "Journey date has passed",
}


class DashboardView(Page):
    def __init__(self, ctx: AppContext):
        super().__init__("Dashboard", "Everything you need at a glance before Tatkal opens.")
        self.ctx = ctx
        self.banner = Banner()
        self.content.addWidget(self.banner)

        grid = QGridLayout()
        grid.setSpacing(16)
        self.content.addLayout(grid)

        # Countdown
        cd = Card("Tatkal countdown")
        self.phase_pill = Pill()
        cd.header.addWidget(self.phase_pill)
        self.countdown = QLabel("--:--:--.---")
        self.countdown.setObjectName("Countdown")
        self.countdown.setAccessibleName("Time remaining until Tatkal opens")
        cd.body.addWidget(self.countdown)
        self.countdown_units = muted("hours : minutes : seconds . milliseconds")
        cd.body.addWidget(self.countdown_units)
        self.schedule_grid = KeyValueGrid(["Category", "Booking date", "Opens at", "Origin departs"])
        cd.body.addWidget(self.schedule_grid)
        grid.addWidget(cd, 0, 0)

        # Journey
        jc = Card("Journey")
        self.journey_grid = KeyValueGrid(["Train", "From", "To", "Journey date", "Class", "Quota"])
        jc.body.addWidget(self.journey_grid)
        jc.body.addStretch(1)
        grid.addWidget(jc, 0, 1)

        # System status
        sc = Card("System status")
        self.status_grid = KeyValueGrid(["Brave browser", "IRCTC session", "Provider", "Network", "IST sync", "Last check"])
        self.pills: dict[str, Pill] = {}
        for key in ["Brave browser", "IRCTC session", "Provider", "Network", "IST sync"]:
            p = Pill()
            self.pills[key] = p
            self.status_grid.set_widget(key, p)
        sc.body.addWidget(self.status_grid)
        sc.body.addStretch(1)
        grid.addWidget(sc, 1, 0)

        # Availability
        ac = Card("Availability")
        self.fresh_pill = Pill()
        ac.header.addWidget(self.fresh_pill)
        self.avail_status = QLabel("No data yet")
        self.avail_status.setObjectName("BigStatus")
        ac.body.addWidget(self.avail_status)
        self.avail_meta = muted("")
        ac.body.addWidget(self.avail_meta)
        self.history = make_table(["Time (IST)", "Status", "Data"])
        self.history.setMaximumHeight(170)
        ac.body.addWidget(self.history)
        grid.addWidget(ac, 1, 1)

        # Readiness
        rc = Card("Readiness checklist")
        self.ready_summary = Pill()
        rc.header.addWidget(self.ready_summary)
        self.ready_holder = QWidget()
        self.ready_box = QGridLayout(self.ready_holder)
        self.ready_box.setContentsMargins(0, 0, 0, 0)
        self.ready_box.setHorizontalSpacing(14)
        self.ready_box.setVerticalSpacing(6)
        rc.body.addWidget(self.ready_holder)
        grid.addWidget(rc, 2, 0, 1, 2)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self.content.addStretch(1)

        # 100 ms local countdown: purely arithmetic on the local clock, no I/O.
        self.timer = QTimer(self)
        self.timer.setInterval(100)
        self.timer.timeout.connect(self._tick)
        self.timer.start()
        self.refresh()

    def handle_event(self, e: Event) -> None:
        if e.type in (EventType.JOURNEY_CHANGED, EventType.READINESS_CHANGED, EventType.AVAILABILITY_RESULT,
                      EventType.BROWSER_STATE_CHANGED, EventType.HEALTH_UPDATED, EventType.MONITOR_STATE_CHANGED,
                      EventType.AVAILABILITY_STALE, EventType.WALLET_CHANGED, EventType.CONFIG_CHANGED,
                      EventType.NETWORK_CHANGED, EventType.TIME_SYNCED):
            self.refresh()

    def _tick(self) -> None:
        cd = self.ctx.scheduler.countdown()
        if cd is None:
            self.countdown.setText("--:--:--.---")
        else:
            self.countdown.setText(cd.format())
        phase = self.ctx.scheduler.phase()
        tone = {Phase.OPEN: "ok", Phase.BOOKING_DAY_PRE_OPEN: "warn", Phase.JOURNEY_PASSED: "bad"}.get(phase, "info")
        if self.phase_pill.text() != PHASE_TEXT[phase]:
            self.phase_pill.set(PHASE_TEXT[phase], tone)

    def refresh(self) -> None:
        ctx = self.ctx
        now = ctx.clock.now_utc()
        j = ctx.journeys.active()
        s = ctx.scheduler.schedule
        if j:
            self.journey_grid.set("Train", f"{j.train_number}  {j.train_name}".strip())
            self.journey_grid.set("From", j.from_station)
            self.journey_grid.set("To", j.to_station)
            self.journey_grid.set("Journey date", j.journey_date.strftime("%a, %d %b %Y"))
            self.journey_grid.set("Class", j.travel_class.label)
            self.journey_grid.set("Quota", j.category.label)
        else:
            for k in self.journey_grid.values:
                self.journey_grid.set(k, "—")
        if s:
            self.schedule_grid.set("Category", s.window.label)
            self.schedule_grid.set("Booking date", s.booking_date.strftime("%a, %d %b %Y"))
            self.schedule_grid.set("Opens at", s.opens_at.strftime("%H:%M IST"))
            self.schedule_grid.set("Origin departs", s.origin_departure_date.strftime("%d %b %Y"))
        else:
            for k in self.schedule_grid.values:
                self.schedule_grid.set(k, "—")

        # system status
        bstate = ctx.brave.state
        self.pills["Brave browser"].set(
            {BrowserState.RUNNING: ("Running", "ok"), BrowserState.NOT_RUNNING: ("Not running", "warn"),
             BrowserState.NOT_INSTALLED: ("Not found", "bad"), BrowserState.UNKNOWN: ("Unknown", "")}[bstate][0],
            {BrowserState.RUNNING: "ok", BrowserState.NOT_RUNNING: "warn",
             BrowserState.NOT_INSTALLED: "bad", BrowserState.UNKNOWN: ""}[bstate])
        login = next((i for i in ctx.readiness.items() if i.id == LOGIN_COMPLETED), None)
        if login and login.done:
            self.pills["IRCTC session"].set("Confirmed by you", "info")
        else:
            self.pills["IRCTC session"].set("Not confirmed", "warn")
        provider = ctx.availability.provider.info
        if not provider.is_real_source:
            self.pills["Provider"].set("Simulated (mock)", "warn")
        else:
            health = ctx.health.provider_health
            self.pills["Provider"].set(f"{provider.name}: {health.value if health else 'checking'}",
                                       "ok" if health and health.value == "OK" else "warn")
        online = ctx.health.network_online
        self.pills["Network"].set({True: ("Online", "ok"), False: ("Offline", "bad"), None: ("Checking", "")}[online][0],
                                  {True: "ok", False: "bad", None: ""}[online])
        ts = ctx.time_sync.status if ctx.time_sync else None
        if ts is None:
            self.pills["IST sync"].set("System clock", "")
        elif ts.synced:
            self.pills["IST sync"].set(f"NTP {ts.offset * 1000:+.0f} ms ({ts.server})", "ok" if abs(ts.offset) < 1 else "warn")
        elif ts.last_attempt:
            self.pills["IST sync"].set("NTP unreachable: using PC clock", "warn")
        else:
            self.pills["IST sync"].set("Syncing…", "")
        snap = ctx.availability.snapshot()
        self.status_grid.set("Last check", f"{ist(snap.last_success_at, '%H:%M:%S')} ({ago(snap.last_success_at, now)})"
                             if snap.last_success_at else "No successful check yet")

        # availability
        q = ctx.journeys.availability_query()
        latest = ctx.availability.latest(q) if q else None
        if latest:
            fr = latest.freshness(now, ctx.availability.config.stale_after_seconds)
            label, tone = freshness_label(latest, now, ctx.availability.config.stale_after_seconds)
            self.fresh_pill.set(label, tone)
            self.avail_status.setText(latest.describe())
            status_tone = STATUS_TONE[latest.status] if fr is Freshness.VERIFIED and not latest.simulated else "warn"
            self.avail_status.setStyleSheet(
                {"ok": "color:#2FBF71;", "warn": "color:#E8B04B;", "bad": "color:#EF6461;"}.get(status_tone, ""))
            self.avail_meta.setText(f"Source: {latest.source} · updated {ist(latest.fetched_at, '%H:%M:%S')} "
                                    f"({ago(latest.fetched_at, now)})")
            rows = ctx.availability.history(q, limit=8, changes_only=True)
            fill_table(self.history, [[ist(r.fetched_at, "%d %b %H:%M:%S"), r.describe(),
                                       ("Simulated" if r.simulated else "Live") + (" · cached" if r.is_cached else "")]
                                      for r, _ in rows])
        else:
            self.fresh_pill.set("No data", "")
            self.avail_status.setText("No data yet")
            self.avail_status.setStyleSheet("")
            self.avail_meta.setText("Start monitoring on the Availability page." if q else "Configure a journey first.")
            fill_table(self.history, [])

        # readiness
        while self.ready_box.count():
            w = self.ready_box.takeAt(0).widget()
            if w:
                w.deleteLater()
        items = ctx.readiness.items()
        done = sum(i.done for i in items)
        self.ready_summary.set(f"{done}/{len(items)} ready", "ok" if done == len(items) else "warn")
        for r, it in enumerate(items):
            text, tone = CHECK_STATE[it.state]
            pill = Pill(text, tone)
            pill.setFixedWidth(84)
            self.ready_box.addWidget(pill, r, 0)
            self.ready_box.addWidget(QLabel(it.label), r, 1)
            self.ready_box.addWidget(muted(CHECK_SOURCE[it.source], wrap=False), r, 2)
            self.ready_box.addWidget(muted(it.detail), r, 3)
        self.ready_box.setColumnStretch(3, 1)

        # banner
        if not provider.is_real_source:
            self.banner.set("Availability data is SIMULATED. No authorized real-time data source is connected yet. "
                            "Check availability on IRCTC yourself.", "warn")
        elif snap.state is MonitorState.STOPPED and snap.last_error:
            self.banner.set(f"Monitoring stopped: {snap.last_error}", "bad")
        else:
            self.banner.set("")
