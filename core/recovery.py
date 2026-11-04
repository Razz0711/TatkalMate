"""Crash recovery support.

* :class:`SessionGuard` writes ``session.json`` at start-up with
  ``clean_shutdown = false`` and flips it on a graceful exit. If the previous
  session never flipped it, the next start reports an unexpected exit.
* :func:`install_crash_handlers` writes a crash report (traceback only, no
  local variables, redacted) for uncaught exceptions on any thread.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

from core.logging_config import REDACTOR
from core.version import __version__

log = logging.getLogger(__name__)


class SessionGuard:
    def __init__(self, path: Path):
        self.path = path
        self.previous_unclean = False
        self.previous: dict | None = None

    def begin(self) -> bool:
        if self.path.exists():
            try:
                self.previous = json.loads(self.path.read_text(encoding="utf-8"))
                self.previous_unclean = not bool(self.previous.get("clean_shutdown", True))
            except (OSError, json.JSONDecodeError):
                self.previous_unclean = True
        self._write(clean=False)
        return self.previous_unclean

    def end(self) -> None:
        self._write(clean=True)

    def _write(self, clean: bool) -> None:
        data = {
            "pid": os.getpid(),
            "version": __version__,
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "clean_shutdown": clean,
        }
        tmp = self.path.with_suffix(".tmp")
        try:
            tmp.write_text(json.dumps(data), encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            log.exception("could not write session state")


def write_crash_report(crash_dir: Path, exc_type, exc, tb) -> Path | None:
    try:
        crash_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        path = crash_dir / f"crash-{stamp}.txt"
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        path.write_text(
            f"TatkalMate {__version__}\n{datetime.now(timezone.utc).isoformat()}\n\n{REDACTOR.redact(text)}",
            encoding="utf-8",
        )
        return path
    except OSError:
        return None


def install_crash_handlers(crash_dir: Path, on_crash: Callable[[str, Path | None], None] | None = None) -> None:
    def handle(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        log.critical("uncaught exception", exc_info=(exc_type, exc, tb))
        path = write_crash_report(crash_dir, exc_type, exc, tb)
        if on_crash:
            try:
                on_crash(f"{exc_type.__name__}: {exc}", path)
            except Exception:
                pass

    def thread_handle(args: threading.ExceptHookArgs) -> None:
        handle(args.exc_type, args.exc_value, args.exc_traceback)

    sys.excepthook = handle
    threading.excepthook = thread_handle


def list_crash_reports(crash_dir: Path) -> list[Path]:
    if not crash_dir.exists():
        return []
    return sorted(crash_dir.glob("crash-*.txt"), reverse=True)
