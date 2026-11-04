"""Builds the full window offscreen and exercises the main flows."""

from datetime import date

import pytest

pytest.importorskip("PySide6.QtWidgets")

from core.paths import AppPaths  # noqa: E402
from models.journey import Journey, TravelClass  # noqa: E402
from models.passenger import Gender, Passenger  # noqa: E402
from services.brave_service import BraveService  # noqa: E402
from services.container import build_context  # noqa: E402


@pytest.fixture
def ctx(tmp_path):
    from core.event_bus import EventBus

    paths = AppPaths(tmp_path).ensure()
    c = build_context(paths, brave=BraveService(EventBus(), platform="linux", which=lambda c: None,
                                                process_lister=lambda: []))
    yield c
    c.shutdown()


def test_main_window_builds_and_navigates(qtbot, ctx):
    from app.bridge import UiBridge
    from app.main_window import MainWindow

    bridge = UiBridge(ctx.bus)
    w = MainWindow(ctx, bridge)
    qtbot.addWidget(w)
    for i in range(w.nav.count()):
        w.nav.setCurrentRow(i)
        assert w.stack.currentIndex() == i

    ctx.journeys.save(Journey("12951", "ST", "NDLS", date(2030, 1, 10), TravelClass.SLEEPER, train_name="Test Exp"))
    ctx.passengers.add(Passenger("Raj Kumar", 22, Gender.MALE))
    qtbot.wait(50)
    dash = w.views["dashboard"]
    dash.refresh()
    assert dash.journey_grid.values["Train"].text().startswith("12951")
    assert dash.schedule_grid.values["Opens at"].text() == "11:00 IST"
    qtbot.waitUntil(lambda: dash.countdown.text() != "--:--:--.---", timeout=1000)
    assert "Simulated" in dash.pills["Provider"].text()

    jv = w.views["journey"]
    jv.train_number.setText("12")
    jv._save()
    assert not jv.banner.isHidden() and "5 digits" in jv.banner.text()

    pv = w.views["passengers"]
    pv.refresh()
    assert pv.table.rowCount() == 1


def test_monitoring_check_now_through_runtime(qtbot, ctx):
    from app.bridge import UiBridge
    from app.main_window import MainWindow
    from providers.mock_provider import MockAvailabilityProvider

    ctx.availability.set_provider(MockAvailabilityProvider(seed=1, failure_rate=0, timeout_rate=0, cached_rate=0))
    ctx.journeys.save(Journey("12951", "ST", "NDLS", date(2030, 1, 10), TravelClass.AC_3_TIER))
    ctx.runtime.start()
    bridge = UiBridge(ctx.bus)
    w = MainWindow(ctx, bridge)
    qtbot.addWidget(w)
    mv = w.views["monitoring"]
    mv._check()
    qtbot.waitUntil(lambda: mv.table.rowCount() == 1, timeout=3000)
    mv._check()  # second immediate check is rate-limited
    qtbot.waitUntil(lambda: "Too soon" in mv.action_banner.text(), timeout=3000)
