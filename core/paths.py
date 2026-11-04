"""Filesystem locations.

Windows: %LOCALAPPDATA%\\TatkalMate (per-user, not roamed).
Other platforms: $XDG_DATA_HOME/TatkalMate or ~/.local/share/TatkalMate.
``TATKALMATE_HOME`` overrides both (used by tests and portable installs).
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from core.version import APP_ID


@dataclass(frozen=True)
class AppPaths:
    root: Path

    @property
    def config_file(self) -> Path:
        return self.root / "config.json"

    @property
    def database_file(self) -> Path:
        return self.root / "tatkalmate.db"

    @property
    def log_dir(self) -> Path:
        return self.root / "logs"

    @property
    def crash_dir(self) -> Path:
        return self.root / "crashes"

    @property
    def session_file(self) -> Path:
        return self.root / "session.json"

    @property
    def lock_file(self) -> Path:
        return self.root / "tatkalmate.lock"

    def ensure(self) -> "AppPaths":
        for p in (self.root, self.log_dir, self.crash_dir):
            p.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            try:
                os.chmod(self.root, 0o700)
            except OSError:
                pass
        return self


def default_root(env: dict[str, str] | None = None, platform: str | None = None) -> Path:
    env = dict(os.environ if env is None else env)
    platform = platform or sys.platform
    override = env.get("TATKALMATE_HOME")
    if override:
        return Path(override).expanduser()
    if platform.startswith("win"):
        base = env.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_ID
    if platform == "darwin":
        return Path.home() / "Library" / "Application Support" / APP_ID
    base = env.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_ID


def resolve_paths(root: Path | None = None) -> AppPaths:
    return AppPaths(root or default_root()).ensure()
