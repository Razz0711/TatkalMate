"""In-app updater for the packaged Windows build.

Flow: check GitHub Releases → download the zip and its ``.sha256`` → verify →
extract into ``<data>/updates/<version>/`` → write a PowerShell helper that
waits for TatkalMate to exit, swaps the app folder (keeping a backup and
rolling back on failure) and restarts the new version.

User data (config, database, logs) lives in ``%LOCALAPPDATA%\\TatkalMate`` and
is never inside the app folder, so it survives updates. Updates are refused
from T-30 to T+15 around a Tatkal opening.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
import zipfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Callable

from core.clock import Clock
from core.paths import AppPaths
from core.scheduler import TatkalScheduler
from core.version import __version__

log = logging.getLogger(__name__)

REPO = "Razz0711/TatkalMate"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
ASSET_NAME = "TatkalMate-windows-x64.zip"
CHECKSUM_NAME = ASSET_NAME + ".sha256"
EXE_NAME = "TatkalMate.exe"
ALLOWED_HOSTS = ("api.github.com", "github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com")
BLOCK_BEFORE = timedelta(minutes=30)
BLOCK_AFTER = timedelta(minutes=15)
MAX_DOWNLOAD = 300 * 1024 * 1024


class UpdateError(Exception):
    pass


@dataclass(frozen=True)
class ReleaseInfo:
    version: str
    tag: str
    notes: str
    zip_url: str
    checksum_url: str | None
    page_url: str


def parse_version(text: str) -> tuple[int, ...]:
    m = re.match(r"^v?(\d+(?:\.\d+)*)", text.strip())
    if not m:
        raise ValueError(f"not a version: {text!r}")
    parts = tuple(int(p) for p in m.group(1).split("."))
    return parts + (0,) * (3 - len(parts))


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


def _check_host(url: str) -> None:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or (parsed.hostname or "") not in ALLOWED_HOSTS:
        raise UpdateError(f"refusing to download from {parsed.hostname!r}")


def http_get(url: str, timeout: float = 30.0, limit: int = MAX_DOWNLOAD) -> bytes:
    _check_host(url)
    req = urllib.request.Request(url, headers={"User-Agent": f"TatkalMate/{__version__}",
                                               "Accept": "application/vnd.github+json, application/octet-stream"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - host allow-listed
            _check_host(resp.geturl())  # after redirects
            data = resp.read(limit + 1)
    except OSError as exc:
        raise UpdateError(f"download failed: {exc}") from exc
    if len(data) > limit:
        raise UpdateError("download too large")
    return data


def parse_release(payload: dict) -> ReleaseInfo:
    if payload.get("draft") or payload.get("prerelease"):
        raise UpdateError("latest release is not a final release")
    assets = {a.get("name"): a.get("browser_download_url") for a in payload.get("assets", [])}
    if ASSET_NAME not in assets:
        raise UpdateError(f"release has no {ASSET_NAME}")
    tag = str(payload.get("tag_name", ""))
    return ReleaseInfo(
        version=".".join(str(p) for p in parse_version(tag)),
        tag=tag,
        notes=str(payload.get("body") or "")[:4000],
        zip_url=assets[ASSET_NAME],
        checksum_url=assets.get(CHECKSUM_NAME),
        page_url=str(payload.get("html_url", "")),
    )


def safe_extract(archive: Path, dest: Path) -> None:
    dest = dest.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            target = (dest / member.filename).resolve()
            if dest not in target.parents and target != dest:
                raise UpdateError(f"unsafe path in archive: {member.filename}")
        zf.extractall(dest)


def find_app_dir(extracted: Path) -> Path:
    for candidate in (extracted / "TatkalMate", extracted):
        if (candidate / EXE_NAME).is_file():
            return candidate
    raise UpdateError(f"{EXE_NAME} not found in the downloaded package")


HELPER_PS1 = r"""
param(
  [Parameter(Mandatory=$true)][int]$WaitPid,
  [Parameter(Mandatory=$true)][string]$NewDir,
  [Parameter(Mandatory=$true)][string]$AppDir,
  [Parameter(Mandatory=$true)][string]$LogFile,
  [switch]$NoStart
)
$ErrorActionPreference = "Stop"
function Log($m) { Add-Content -Path $LogFile -Value ("{0:u} {1}" -f (Get-Date), $m) }
$backup = "$AppDir.old"
try {
  Log "waiting for process $WaitPid"
  try { Wait-Process -Id $WaitPid -Timeout 120 -ErrorAction Stop } catch { }
  if (Get-Process -Id $WaitPid -ErrorAction SilentlyContinue) { throw "TatkalMate did not exit" }
  Start-Sleep -Milliseconds 500
  if (Test-Path $backup) { Remove-Item $backup -Recurse -Force }
  $moved = $false
  for ($i = 0; $i -lt 20 -and -not $moved; $i++) {
    try { Move-Item -Path $AppDir -Destination $backup -ErrorAction Stop; $moved = $true }
    catch { Start-Sleep -Milliseconds 500 }
  }
  if (-not $moved) { throw "could not move the current version (files in use?)" }
  try {
    try { Move-Item -Path $NewDir -Destination $AppDir -ErrorAction Stop }
    catch {
      # Different drive: Move-Item can't move folders across volumes, so copy instead.
      Copy-Item -Path $NewDir -Destination $AppDir -Recurse -ErrorAction Stop
      Remove-Item $NewDir -Recurse -Force -ErrorAction SilentlyContinue
    }
    if (-not (Test-Path (Join-Path $AppDir "TatkalMate.exe"))) { throw "new version is incomplete" }
  } catch {
    Log "install failed, rolling back: $_"
    if (Test-Path $AppDir) { Remove-Item $AppDir -Recurse -Force }
    Move-Item -Path $backup -Destination $AppDir
    throw
  }
  Log "installed new version"
  if (-not $NoStart) { Start-Process -FilePath (Join-Path $AppDir "TatkalMate.exe") -WorkingDirectory $AppDir }
  Remove-Item $backup -Recurse -Force -ErrorAction SilentlyContinue
  Log "done"
  exit 0
} catch {
  Log "update failed: $_"
  if (-not $NoStart -and (Test-Path (Join-Path $AppDir "TatkalMate.exe"))) {
    Start-Process -FilePath (Join-Path $AppDir "TatkalMate.exe") -WorkingDirectory $AppDir
  }
  exit 1
}
"""


class UpdateService:
    def __init__(self, paths: AppPaths, scheduler: TatkalScheduler, clock: Clock,
                 fetch: Callable[[str], bytes] = http_get,
                 app_dir: Path | None = None, frozen: bool | None = None, platform: str | None = None):
        self._paths = paths
        self._scheduler = scheduler
        self._clock = clock
        self._fetch = fetch
        self.frozen = getattr(sys, "frozen", False) if frozen is None else frozen
        self.platform = platform or sys.platform
        self.app_dir = (app_dir or Path(sys.executable).resolve().parent)
        self.latest: ReleaseInfo | None = None
        self.last_error: str | None = None

    @property
    def updates_dir(self) -> Path:
        return self._paths.root / "updates"

    # ----- policy -----------------------------------------------------
    def blocked_reason(self) -> str | None:
        s = self._scheduler.schedule
        if s is None:
            return None
        now = self._clock.now_utc()
        start, end = s.opens_at_utc - BLOCK_BEFORE, s.opens_at_utc + BLOCK_AFTER
        if start <= now <= end:
            return (f"Updates are paused from 30 min before to 15 min after Tatkal opens "
                    f"({s.opens_at:%H:%M} IST). Try again after {(s.opens_at + BLOCK_AFTER):%H:%M} IST.")
        return None

    def can_install(self) -> str | None:
        """None if installing is possible, else the reason it isn't."""
        if not self.frozen:
            return "You're running from source. Update with 'git pull' instead."
        if not self.platform.startswith("win"):
            return "Automatic install is only supported for the Windows build."
        data = self._paths.root.resolve()
        app = self.app_dir.resolve()
        if data == app or app in data.parents or data in app.parents:
            return (f"TatkalMate is installed inside its data folder ({data}). Move the app folder elsewhere "
                    "(e.g. C:\\Apps\\TatkalMate) so updates can't touch your data.")
        try:
            probe = app.parent / ".tatkalmate-write-test"
            probe.write_text("x")
            probe.unlink()
        except OSError:
            return f"No permission to replace {app}. Move TatkalMate to a folder you own (e.g. C:\\Apps)."
        return self.blocked_reason()

    # ----- check / download -------------------------------------------
    def check(self) -> ReleaseInfo | None:
        """Return the newer release, or None if up to date. Raises UpdateError."""
        try:
            payload = json.loads(self._fetch(LATEST_URL).decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as exc:
            raise UpdateError("unexpected response from GitHub") from exc
        info = parse_release(payload)
        self.latest = info if is_newer(info.version) else None
        log.info("update check", extra={"latest": info.version, "current": __version__,
                                         "available": self.latest is not None})
        return self.latest

    def download(self, info: ReleaseInfo, progress: Callable[[str], None] = lambda _m: None) -> Path:
        """Download, verify and extract. Returns the folder that will replace the app folder."""
        if info.checksum_url is None:
            raise UpdateError("release has no checksum file; refusing to install an unverified download")
        work = self.updates_dir / info.version
        if work.exists():
            shutil.rmtree(work)
        work.mkdir(parents=True)
        progress("Downloading checksum…")
        expected = self._fetch(info.checksum_url).decode("ascii", "replace").split()[0].strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise UpdateError("checksum file is malformed")
        progress("Downloading update…")
        data = self._fetch(info.zip_url)
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected:
            raise UpdateError("checksum mismatch: the download is corrupt or was tampered with")
        archive = work / ASSET_NAME
        archive.write_bytes(data)
        progress("Extracting…")
        extract_to = work / "extracted"
        safe_extract(archive, extract_to)
        app = find_app_dir(extract_to)
        archive.unlink()
        log.info("update downloaded and verified", extra={"version": info.version})
        return app

    # ----- install ------------------------------------------------------
    def write_helper(self) -> Path:
        self.updates_dir.mkdir(parents=True, exist_ok=True)
        helper = self.updates_dir / "apply_update.ps1"
        helper.write_text(HELPER_PS1, encoding="utf-8")
        return helper

    def helper_command(self, new_dir: Path, pid: int | None = None, no_start: bool = False) -> list[str]:
        cmd = ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass",
               "-WindowStyle", "Hidden", "-File", str(self.write_helper()),
               "-WaitPid", str(pid or os.getpid()), "-NewDir", str(new_dir), "-AppDir", str(self.app_dir),
               "-LogFile", str(self.updates_dir / "update.log")]
        if no_start:
            cmd.append("-NoStart")
        return cmd

    def start_install(self, new_dir: Path, launcher: Callable[..., object] = subprocess.Popen) -> None:
        """Launch the helper. The caller must quit the app right after."""
        reason = self.can_install()
        if reason:
            raise UpdateError(reason)
        flags = getattr(subprocess, "DETACHED_PROCESS", 0x08) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        launcher(self.helper_command(new_dir), creationflags=flags, close_fds=True,
                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        log.info("update helper started")

    def cleanup(self) -> None:
        """Remove leftovers from previous updates (called at start-up)."""
        if not self.updates_dir.exists():
            return
        for child in self.updates_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
