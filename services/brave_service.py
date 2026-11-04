"""Brave browser integration.

What it does:
* finds brave.exe (settings override, standard install folders, App Paths
  registry entries);
* launches Brave with the official IRCTC URL **in the user's normal profile**:
  no ``--user-data-dir``, no ``--remote-debugging-port``, no automation flags;
* watches whether a Brave process is running (local process list only) and
  reports unexpected closure;
* brings an existing Brave window to the foreground (Windows).

What it deliberately does not do: attach to the browser, read pages, cookies,
passwords, OTPs or tokens, fill forms, or click anything. The user interacts
with IRCTC themselves.
"""

from __future__ import annotations

import logging
import ntpath
import os
import shutil
import subprocess
import sys
import threading
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Callable, Iterable

from core.clock import Clock, SystemClock
from core.config import IRCTC_HOME_URL
from core.event_bus import EventBus, EventType
from core.exceptions import BrowserLaunchError, BrowserNotFoundError
from services.platform import win32_window

log = logging.getLogger(__name__)

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None  # type: ignore[assignment]

_WIN_RELATIVE = r"BraveSoftware\Brave-Browser\Application\brave.exe"
_WIN_ENV_ROOTS = ("PROGRAMFILES", "PROGRAMW6432", "PROGRAMFILES(X86)", "LOCALAPPDATA")
_POSIX_COMMANDS = ("brave-browser", "brave", "brave-browser-stable")
_MAC_PATH = "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
_PROCESS_NAMES = {"brave.exe", "brave", "brave-browser", "brave browser"}


class BrowserState(str, Enum):
    NOT_INSTALLED = "NOT_INSTALLED"
    NOT_RUNNING = "NOT_RUNNING"
    RUNNING = "RUNNING"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class BraveInstallation:
    path: str
    source: str


ProcessLister = Callable[[], Iterable[tuple[int, str]]]


def _psutil_processes() -> Iterable[tuple[int, str]]:
    if psutil is None:
        raise RuntimeError("psutil not installed")
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            yield proc.info["pid"], (proc.info["name"] or "")
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue


class BraveService:
    def __init__(
        self,
        bus: EventBus,
        path_override: Callable[[], str] = lambda: "",
        platform: str | None = None,
        env: dict[str, str] | None = None,
        file_exists: Callable[[str], bool] = os.path.isfile,
        which: Callable[[str], str | None] = shutil.which,
        registry_reader: Callable[[], list[str]] = win32_window.read_app_path_registry,
        process_lister: ProcessLister | None = None,
        launcher: Callable[..., object] = subprocess.Popen,
        focuser: Callable[[set[int]], bool] = win32_window.focus_process_windows,
        clock: Clock | None = None,
    ):
        self._bus = bus
        self._path_override = path_override
        self._platform = platform or sys.platform
        self._env = dict(os.environ if env is None else env)
        self._exists = file_exists
        self._which = which
        self._registry_reader = registry_reader
        self._process_lister = process_lister or _psutil_processes
        self._launcher = launcher
        self._focuser = focuser
        self._clock = clock or SystemClock()
        self._lock = threading.Lock()
        self._state = BrowserState.UNKNOWN
        self._installation: BraveInstallation | None = None
        self.last_launch_at: datetime | None = None
        self.last_error: str | None = None

    @property
    def is_windows(self) -> bool:
        return self._platform.startswith("win")

    @property
    def state(self) -> BrowserState:
        with self._lock:
            return self._state

    @property
    def installation(self) -> BraveInstallation | None:
        return self._installation

    # ----- detection ------------------------------------------------------
    def candidate_paths(self) -> list[BraveInstallation]:
        out: list[BraveInstallation] = []
        override = (self._path_override() or "").strip().strip('"')
        if override:
            out.append(BraveInstallation(override, "settings"))
        if self.is_windows:
            for var in _WIN_ENV_ROOTS:
                root = self._env.get(var)
                if root:
                    out.append(BraveInstallation(ntpath.join(root, _WIN_RELATIVE), f"%{var}%"))
            try:
                for p in self._registry_reader():
                    out.append(BraveInstallation(p, "registry"))
            except Exception:
                log.warning("could not read Brave registry entries")
        elif self._platform == "darwin":
            out.append(BraveInstallation(_MAC_PATH, "applications"))
        else:
            for cmd in _POSIX_COMMANDS:
                found = self._which(cmd)
                if found:
                    out.append(BraveInstallation(found, "PATH"))
        seen: set[str] = set()
        unique = []
        for c in out:
            k = c.path.lower() if self.is_windows else c.path
            if k not in seen:
                seen.add(k)
                unique.append(c)
        return unique

    def detect(self) -> BraveInstallation | None:
        for candidate in self.candidate_paths():
            if self._exists(candidate.path):
                if self._installation != candidate:
                    log.info("brave detected", extra={"source": candidate.source})
                self._installation = candidate
                return candidate
        self._installation = None
        return None

    def brave_pids(self) -> set[int] | None:
        try:
            return {pid for pid, name in self._process_lister() if name.lower() in _PROCESS_NAMES}
        except Exception:
            return None

    def is_running(self) -> bool | None:
        pids = self.brave_pids()
        return None if pids is None else bool(pids)

    # ----- state watch ----------------------------------------------------
    def poll(self) -> BrowserState:
        if self._installation is None:
            self.detect()
        running = self.is_running()
        if running is None:
            new = BrowserState.UNKNOWN
        elif running:
            new = BrowserState.RUNNING  # running even if the exe path is unknown
        elif self._installation is None:
            new = BrowserState.NOT_INSTALLED
        else:
            new = BrowserState.NOT_RUNNING
        with self._lock:
            old, self._state = self._state, new
        if old != new:
            log.info("browser state", extra={"from": old.value, "to": new.value})
            self._bus.publish(EventType.BROWSER_STATE_CHANGED, state=new, previous=old)
            if old is BrowserState.RUNNING and new in (BrowserState.NOT_RUNNING, BrowserState.NOT_INSTALLED):
                self._bus.publish(EventType.BROWSER_DISCONNECTED)
        return new

    async def watch(self, interval: Callable[[], float]) -> None:
        import asyncio

        while True:
            await asyncio.to_thread(self.poll)
            await asyncio.sleep(interval())

    # ----- actions --------------------------------------------------------
    def launch(self, url: str = IRCTC_HOME_URL) -> None:
        if not url.startswith("https://"):
            raise BrowserLaunchError("only https URLs are opened")
        install = self.detect()
        if install is None:
            raise BrowserNotFoundError(
                "Brave was not found. Install Brave or set the path to brave.exe in Settings."
            )
        kwargs: dict[str, object] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL,
                                     "stderr": subprocess.DEVNULL, "close_fds": True}
        if self.is_windows:
            # Detach so closing TatkalMate never closes the browser.
            kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x08) | getattr(
                subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        else:
            kwargs["start_new_session"] = True
        try:
            # Normal profile, no debugging port, no automation switches.
            self._launcher([install.path, url], **kwargs)
        except OSError as exc:
            self.last_error = str(exc)
            log.error("brave launch failed", extra={"error": type(exc).__name__})
            raise BrowserLaunchError(f"Could not start Brave: {exc}") from exc
        self.last_launch_at = self._clock.now_utc()
        self.last_error = None
        log.info("brave launched")

    def bring_to_foreground(self) -> tuple[bool, str]:
        pids = self.brave_pids()
        if not pids:
            return False, "Brave is not running."
        if not self.is_windows:
            return False, "Bringing windows to the front is supported on Windows only."
        try:
            ok = self._focuser(pids)
        except Exception as exc:
            log.warning("focus failed", extra={"error": type(exc).__name__})
            return False, "Windows refused to switch windows."
        return (True, "Brave brought to the front.") if ok else (
            False, "No visible Brave window was found (it may be minimised to the tray).")
