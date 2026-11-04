"""Exercises the real Windows APIs. Skipped elsewhere; runs on the windows-latest CI runner."""

import sys

import pytest

pytestmark = pytest.mark.skipif(not sys.platform.startswith("win"), reason="Windows only")


def test_dpapi_roundtrip():
    from core.secure_codec import SecureCodec

    codec = SecureCodec()
    assert codec.mode == "dpapi"
    stored = codec.encode("Raj Kumar")
    assert stored.startswith("dpapi:") and "Raj" not in stored
    assert codec.decode(stored) == "Raj Kumar"


def test_registry_reader_does_not_crash():
    from services.platform.win32_window import read_app_path_registry

    assert isinstance(read_app_path_registry(), list)


def test_focus_unknown_pid_returns_false():
    from services.platform.win32_window import focus_process_windows

    assert focus_process_windows({2**31 - 7}) is False


def test_real_brave_detection_and_process_listing_run():
    from core.event_bus import EventBus
    from services.brave_service import BraveService, BrowserState

    svc = BraveService(EventBus())
    svc.detect()  # Brave is usually absent on CI; must not raise
    assert svc.poll() in (BrowserState.NOT_INSTALLED, BrowserState.RUNNING, BrowserState.NOT_RUNNING)


def test_default_data_dir_is_localappdata(monkeypatch):
    from core.paths import default_root

    monkeypatch.delenv("TATKALMATE_HOME", raising=False)
    assert "AppData" in str(default_root()) or "LOCALAPPDATA" in str(default_root())


def test_update_helper_swaps_folder_and_keeps_data(tmp_path):
    """Runs the real PowerShell helper against a fake install."""
    import subprocess

    from core.clock import SystemClock
    from core.paths import AppPaths
    from core.scheduler import TatkalScheduler
    from services.update_service import UpdateService

    app = tmp_path / "Apps" / "TatkalMate"
    app.mkdir(parents=True)
    (app / "TatkalMate.exe").write_text("old")
    data = AppPaths(tmp_path / "LocalAppData" / "TatkalMate").ensure()
    data.config_file.write_text('{"keep": true}')
    new = tmp_path / "LocalAppData" / "TatkalMate" / "updates" / "9.0.0" / "extracted" / "TatkalMate"
    new.mkdir(parents=True)
    (new / "TatkalMate.exe").write_text("new")

    svc = UpdateService(data, TatkalScheduler(SystemClock()), SystemClock(), app_dir=app, frozen=True)
    dead = subprocess.Popen(["cmd", "/c", "exit 0"])
    dead.wait()
    cmd = [c for c in svc.helper_command(new, pid=dead.pid, no_start=True) if c not in ("-WindowStyle", "Hidden")]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    log = (data.root / "updates" / "update.log").read_text()
    assert result.returncode == 0, log + result.stderr
    assert (app / "TatkalMate.exe").read_text() == "new"
    assert not (app.parent / "TatkalMate.old").exists()
    assert data.config_file.read_text() == '{"keep": true}'
