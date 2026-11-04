"""Render every page offscreen to PNG (used for docs and visual checks).

Usage: QT_QPA_PLATFORM=offscreen python scripts/screenshots.py OUT_DIR
"""

from __future__ import annotations

import sys
import tempfile
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from app.bridge import UiBridge  # noqa: E402
from app.main_window import MainWindow  # noqa: E402
from app.navigation import NAV_ITEMS  # noqa: E402
from app.styles import stylesheet  # noqa: E402
from core.event_bus import EventBus  # noqa: E402
from core.logging_config import setup_logging  # noqa: E402
from core.paths import AppPaths  # noqa: E402
from core.scheduler import IST  # noqa: E402
from models.journey import Journey, TatkalCategory, TravelClass  # noqa: E402
from models.passenger import BerthPreference, Gender, Passenger  # noqa: E402
from providers.mock_provider import MockAvailabilityProvider  # noqa: E402
from services.brave_service import BraveService  # noqa: E402
from services.container import build_context  # noqa: E402
from services.readiness_service import PASSENGERS_REVIEWED  # noqa: E402


def main(out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    paths = AppPaths(Path(tempfile.mkdtemp())).ensure()
    setup_logging(paths.log_dir, console=False)
    app = QApplication([])
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())
    brave = BraveService(EventBus(), platform="win32",
                         env={"PROGRAMFILES": r"C:\Program Files"},
                         file_exists=lambda p: p.endswith("brave.exe"), registry_reader=lambda: [],
                         process_lister=lambda: [(4242, "brave.exe")])
    ctx = build_context(paths, brave=brave)
    ctx.availability.set_provider(MockAvailabilityProvider(seed=7, failure_rate=0, timeout_rate=0, latency=(0, 0)))
    tomorrow = (ctx.clock.now_utc().astimezone(IST) + timedelta(days=2)).date()
    ctx.journeys.save(Journey("12951", "ST", "NDLS", tomorrow, TravelClass.AC_3_TIER, TatkalCategory.TATKAL,
                              train_name="Mumbai Rajdhani"))
    ctx.passengers.add(Passenger("Raj Kumar", 22, Gender.MALE, BerthPreference.LOWER))
    ctx.passengers.add(Passenger("Asha Devi", 47, Gender.FEMALE, BerthPreference.LOWER))
    ctx.readiness.confirm(PASSENGERS_REVIEWED)
    ctx.wallet.set_estimated_fare(4200)
    ctx.brave.poll()
    ctx.runtime.start()
    q = ctx.journeys.availability_query()
    for _ in range(6):  # seed history (bypasses the floor only because we reset the slot)
        ctx.runtime.submit(ctx.availability.check_now(q)).result(5)
        ctx.availability._last_request_mono = None
    import asyncio
    ctx.runtime.submit(ctx.health.run_checks()).result(15)

    bridge = UiBridge(ctx.bus)
    w = MainWindow(ctx, bridge)
    w.resize(1360, 900)
    w.show()

    def shoot(i: int = 0) -> None:
        if i >= len(NAV_ITEMS):
            ctx.shutdown()
            app.quit()
            return
        w.nav.setCurrentRow(i)
        QTimer.singleShot(250, lambda: (w.grab().save(str(out / f"{i + 1:02d}-{NAV_ITEMS[i].key}.png")), shoot(i + 1)))

    QTimer.singleShot(400, shoot)
    app.exec()


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "screenshots"))
