import hashlib
import io
import json
import sys
import zipfile
from datetime import date, datetime
from pathlib import Path

import pytest

from core.clock import FakeClock
from core.paths import AppPaths
from core.scheduler import IST, TatkalScheduler
from models.journey import Journey, TravelClass
from services.update_service import (
    ASSET_NAME,
    CHECKSUM_NAME,
    LATEST_URL,
    UpdateError,
    UpdateService,
    is_newer,
    parse_release,
    parse_version,
    safe_extract,
)

ZIP_URL = "https://github.com/Razz0711/TatkalMate/releases/download/v9.0.0/" + ASSET_NAME
SUM_URL = ZIP_URL + ".sha256"


def make_zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def release(tag="v9.0.0", with_sum=True, **kw):
    assets = [{"name": ASSET_NAME, "browser_download_url": ZIP_URL}]
    if with_sum:
        assets.append({"name": CHECKSUM_NAME, "browser_download_url": SUM_URL})
    return {"tag_name": tag, "assets": assets, "body": "notes", "html_url": "https://github.com/x", **kw}


def service(tmp_path, responses, clock=None, frozen=True, platform="win32", app_dir=None):
    paths = AppPaths(tmp_path / "data").ensure()
    clock = clock or FakeClock(datetime(2026, 11, 19, 8, 0, tzinfo=IST))
    sched = TatkalScheduler(clock)
    app = app_dir or (tmp_path / "apps" / "TatkalMate")
    app.mkdir(parents=True, exist_ok=True)

    def fetch(url):
        if url not in responses:
            raise UpdateError(f"404 {url}")
        return responses[url]

    return UpdateService(paths, sched, clock, fetch=fetch, app_dir=app, frozen=frozen, platform=platform), sched, clock


def test_version_parsing_and_compare():
    assert parse_version("v0.10.1") == (0, 10, 1)
    assert parse_version("1.2") == (1, 2, 0)
    assert is_newer("v0.10.0", "0.9.9") and not is_newer("v0.3.0", "0.3.0") and not is_newer("0.2.9", "0.3.0")
    with pytest.raises(ValueError):
        parse_version("latest")


def test_parse_release_rejects_prerelease_and_missing_asset():
    with pytest.raises(UpdateError):
        parse_release(release(prerelease=True))
    with pytest.raises(UpdateError):
        parse_release({"tag_name": "v1.0.0", "assets": []})


def test_check_reports_newer_and_up_to_date(tmp_path):
    svc, *_ = service(tmp_path, {LATEST_URL: json.dumps(release()).encode()})
    assert svc.check().version == "9.0.0"
    svc, *_ = service(tmp_path, {LATEST_URL: json.dumps(release(tag="v0.0.1")).encode()})
    assert svc.check() is None


def test_download_verifies_checksum_and_extracts(tmp_path):
    pkg = make_zip({"TatkalMate/TatkalMate.exe": b"MZ new", "TatkalMate/_internal/x.dll": b"x"})
    good = hashlib.sha256(pkg).hexdigest() + "  " + ASSET_NAME
    svc, *_ = service(tmp_path, {ZIP_URL: pkg, SUM_URL: good.encode()})
    new_dir = svc.download(parse_release(release()))
    assert (new_dir / "TatkalMate.exe").read_bytes() == b"MZ new"
    assert new_dir.is_relative_to(svc.updates_dir)


def test_download_rejects_bad_checksum_and_missing_checksum(tmp_path):
    pkg = make_zip({"TatkalMate/TatkalMate.exe": b"MZ"})
    svc, *_ = service(tmp_path, {ZIP_URL: pkg, SUM_URL: ("0" * 64).encode()})
    with pytest.raises(UpdateError, match="checksum mismatch"):
        svc.download(parse_release(release()))
    with pytest.raises(UpdateError, match="no checksum"):
        svc.download(parse_release(release(with_sum=False)))


def test_download_rejects_package_without_exe(tmp_path):
    pkg = make_zip({"readme.txt": b"hi"})
    svc, *_ = service(tmp_path, {ZIP_URL: pkg, SUM_URL: hashlib.sha256(pkg).hexdigest().encode()})
    with pytest.raises(UpdateError, match="not found"):
        svc.download(parse_release(release()))


def test_zip_slip_is_blocked(tmp_path):
    archive = tmp_path / "evil.zip"
    archive.write_bytes(make_zip({"../../evil.txt": b"x"}))
    with pytest.raises(UpdateError, match="unsafe"):
        safe_extract(archive, tmp_path / "out")


def test_install_blocked_around_tatkal_opening(tmp_path):
    clock = FakeClock(datetime(2026, 11, 19, 9, 31, tzinfo=IST))  # AC opens 10:00
    svc, sched, clock = service(tmp_path, {}, clock=clock)
    sched.set_journey(Journey("12951", "ST", "NDLS", date(2026, 11, 20), TravelClass.AC_3_TIER))
    assert "paused" in svc.can_install()
    clock.advance(43 * 60)  # 10:14
    assert svc.blocked_reason()
    clock.advance(2 * 60)  # 10:16
    assert svc.can_install() is None
    clock.set_wall(datetime(2026, 11, 19, 9, 29, tzinfo=IST))
    assert svc.can_install() is None


def test_install_refused_from_source_non_windows_or_inside_data_dir(tmp_path):
    svc, *_ = service(tmp_path, {}, frozen=False)
    assert "source" in svc.can_install()
    svc, *_ = service(tmp_path, {}, platform="linux")
    assert "Windows" in svc.can_install()
    svc, *_ = service(tmp_path, {}, app_dir=tmp_path / "data" / "app")
    assert "data folder" in svc.can_install()


def test_start_install_launches_detached_helper(tmp_path):
    svc, *_ = service(tmp_path, {})
    calls = []
    new_dir = tmp_path / "new"
    svc.start_install(new_dir, launcher=lambda cmd, **kw: calls.append((cmd, kw)))
    cmd, kw = calls[0]
    assert cmd[0] == "powershell.exe" and "-File" in cmd
    assert cmd[cmd.index("-AppDir") + 1] == str(svc.app_dir)
    assert cmd[cmd.index("-NewDir") + 1] == str(new_dir)
    assert kw["creationflags"]
    assert Path(cmd[cmd.index("-File") + 1]).read_text().startswith("\nparam(")


def test_user_data_lives_outside_app_folder_on_windows():
    from core.paths import default_root

    root = default_root(env={"LOCALAPPDATA": r"C:\Users\raj\AppData\Local"}, platform="win32")
    assert str(root).replace("/", "\\").endswith(r"AppData\Local\TatkalMate")
