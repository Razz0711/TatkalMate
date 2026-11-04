from __future__ import annotations

from PySide6.QtWidgets import QHBoxLayout

from app.components.formatting import ist
from app.components.widgets import Banner, Card, KeyValueGrid, Page, Pill, button, muted
from core.event_bus import Event, EventType
from core.exceptions import BrowserError
from services.brave_service import BrowserState
from services.container import AppContext

STATE = {
    BrowserState.RUNNING: ("Running", "ok"),
    BrowserState.NOT_RUNNING: ("Not running", "warn"),
    BrowserState.NOT_INSTALLED: ("Not found", "bad"),
    BrowserState.UNKNOWN: ("Unknown", ""),
}


class BrowserView(Page):
    def __init__(self, ctx: AppContext):
        super().__init__("Browser management",
                         "Opens Brave with your normal profile so your existing sessions and saved settings are "
                         "used. TatkalMate does not control the browser, read pages, or access cookies or passwords.")
        self.ctx = ctx
        self.banner = Banner()
        self.content.addWidget(self.banner)
        card = Card("Brave")
        self.pill = Pill()
        card.header.addWidget(self.pill)
        self.grid = KeyValueGrid(["Executable", "Found via", "Last launched", "Opens"])
        card.body.addWidget(self.grid)
        row = QHBoxLayout()
        self.launch_btn = button("Launch Brave and open IRCTC", primary=True)
        self.front_btn = button("Bring Brave to front")
        self.detect_btn = button("Re-detect")
        row.addWidget(self.launch_btn)
        row.addWidget(self.front_btn)
        row.addWidget(self.detect_btn)
        row.addStretch(1)
        card.body.addLayout(row)
        self.content.addWidget(card)

        info = Card("What TatkalMate does and does not do")
        for t in [
            "✓ Finds Brave in the standard Windows folders, the App Paths registry key, or the path you set in Settings.",
            "✓ Launches Brave with the official IRCTC URL in your normal profile (no automation or debugging flags).",
            "✓ Watches whether Brave is running and alerts you if it closes unexpectedly.",
            "✗ Never logs in for you, fills forms, solves CAPTCHAs or clicks anything on IRCTC.",
            "✗ Never reads or stores cookies, passwords, OTPs or session tokens.",
        ]:
            info.body.addWidget(muted(t))
        self.content.addWidget(info)
        self.content.addStretch(1)

        self.launch_btn.clicked.connect(self._launch)
        self.front_btn.clicked.connect(self._front)
        self.detect_btn.clicked.connect(self._detect)
        self.refresh()

    def handle_event(self, e: Event) -> None:
        if e.type in (EventType.BROWSER_STATE_CHANGED, EventType.CONFIG_CHANGED):
            self.refresh()
        if e.type is EventType.BROWSER_DISCONNECTED:
            self.banner.set("Brave closed. If that was unexpected, relaunch it and log in to IRCTC again.", "warn")

    def refresh(self) -> None:
        b = self.ctx.brave
        text, tone = STATE[b.state]
        self.pill.set(text, tone)
        inst = b.installation
        self.grid.set("Executable", inst.path if inst else "Not found. Set the path to brave.exe in Settings.")
        self.grid.set("Found via", inst.source if inst else "—")
        self.grid.set("Last launched", ist(b.last_launch_at) if b.last_launch_at else "Not this session")
        self.grid.set("Opens", self.ctx.config.config.browser.irctc_url)
        self.front_btn.setEnabled(b.state is BrowserState.RUNNING)

    def _launch(self) -> None:
        try:
            self.ctx.brave.launch(self.ctx.config.config.browser.irctc_url)
            self.banner.set("Brave launched. Log in to IRCTC yourself, then confirm it on the Preparation page.", "ok")
        except BrowserError as exc:
            self.banner.set(str(exc), "bad")
        self.refresh()

    def _front(self) -> None:
        ok, msg = self.ctx.brave.bring_to_foreground()
        self.banner.set(msg, "ok" if ok else "warn")

    def _detect(self) -> None:
        inst = self.ctx.brave.detect()
        self.ctx.brave.poll()
        self.banner.set(f"Found Brave at {inst.path}" if inst else "Brave was not found.", "ok" if inst else "bad")
        self.refresh()
