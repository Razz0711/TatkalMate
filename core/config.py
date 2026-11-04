"""Persistent configuration.

Stored as JSON in the per-user data directory and written atomically (temp
file + ``os.replace``) so a crash mid-write cannot corrupt it. Unknown keys are
ignored and invalid values fall back to defaults, so an old or hand-edited file
never prevents start-up. Secrets (provider API keys) are *not* stored here;
they come from environment variables or a local ``.env`` file.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from dataclasses import asdict, dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any, Callable

from core.exceptions import ConfigError

log = logging.getLogger(__name__)

IRCTC_HOME_URL = "https://www.irctc.co.in/nget/train-search"

# Compliance floor: no provider may be polled more often than this, whatever
# the user or provider configuration says.
HARD_MIN_POLL_SECONDS = 60.0


@dataclass
class MonitoringConfig:
    provider: str = "mock"  # "mock" | "authorized"
    poll_interval_seconds: float = 300.0
    stale_after_seconds: float = 900.0
    request_timeout_seconds: float = 15.0
    backoff_base_seconds: float = 30.0
    backoff_max_seconds: float = 1800.0
    failure_alert_threshold: int = 3


@dataclass
class NotificationConfig:
    desktop_enabled: bool = True
    sound_enabled: bool = True
    dedupe_window_seconds: float = 300.0
    opening_reminders_enabled: bool = True
    reminder_minutes: list[int] = field(default_factory=lambda: [30, 10, 2])


@dataclass
class BrowserConfig:
    brave_path_override: str = ""
    irctc_url: str = IRCTC_HOME_URL
    watch_interval_seconds: float = 3.0


@dataclass
class NetworkConfig:
    probe_host: str = "1.1.1.1"
    probe_port: int = 443
    probe_interval_seconds: float = 30.0


@dataclass
class UiConfig:
    start_page: str = "dashboard"
    minimize_to_tray: bool = False


@dataclass
class AppConfig:
    monitoring: MonitoringConfig = field(default_factory=MonitoringConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    browser: BrowserConfig = field(default_factory=BrowserConfig)
    network: NetworkConfig = field(default_factory=NetworkConfig)
    ui: UiConfig = field(default_factory=UiConfig)

    def validated(self) -> "AppConfig":
        m = self.monitoring
        m.poll_interval_seconds = max(HARD_MIN_POLL_SECONDS, float(m.poll_interval_seconds))
        m.stale_after_seconds = max(m.poll_interval_seconds, float(m.stale_after_seconds))
        m.request_timeout_seconds = min(60.0, max(3.0, float(m.request_timeout_seconds)))
        m.backoff_base_seconds = max(5.0, float(m.backoff_base_seconds))
        m.backoff_max_seconds = max(m.backoff_base_seconds, float(m.backoff_max_seconds))
        m.failure_alert_threshold = max(1, int(m.failure_alert_threshold))
        if m.provider not in ("mock", "authorized"):
            m.provider = "mock"
        n = self.notifications
        n.dedupe_window_seconds = max(10.0, float(n.dedupe_window_seconds))
        n.reminder_minutes = sorted({int(x) for x in n.reminder_minutes if 0 < int(x) <= 24 * 60}, reverse=True)
        b = self.browser
        b.watch_interval_seconds = min(30.0, max(1.0, float(b.watch_interval_seconds)))
        if not b.irctc_url.startswith("https://"):
            b.irctc_url = IRCTC_HOME_URL
        net = self.network
        net.probe_interval_seconds = max(10.0, float(net.probe_interval_seconds))
        net.probe_port = int(net.probe_port)
        return self


def _from_dict(cls: type, data: Any) -> Any:
    obj = cls()
    if not isinstance(data, dict):
        return obj
    for f in fields(cls):
        if f.name not in data:
            continue
        current = getattr(obj, f.name)
        raw = data[f.name]
        if is_dataclass(current):
            setattr(obj, f.name, _from_dict(type(current), raw))
            continue
        try:
            if isinstance(current, bool):
                if not isinstance(raw, bool):
                    raise TypeError
                value: Any = raw
            elif isinstance(current, (int, float)):
                if isinstance(raw, bool):
                    raise TypeError
                value = type(current)(raw)
            elif isinstance(current, list):
                if not isinstance(raw, list):
                    raise TypeError
                value = list(raw)
            elif isinstance(current, str):
                if not isinstance(raw, str):
                    raise TypeError
                value = raw
            else:
                value = raw
            setattr(obj, f.name, value)
        except (TypeError, ValueError):
            log.warning("ignoring invalid config value", extra={"key": f"{cls.__name__}.{f.name}"})
    return obj


class ConfigManager:
    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.RLock()
        self._listeners: list[Callable[[AppConfig], None]] = []
        self.config = self.load()

    def load(self) -> AppConfig:
        if not self.path.exists():
            return AppConfig().validated()
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            backup = self.path.with_suffix(".corrupt.json")
            log.exception("config unreadable; using defaults and keeping a backup")
            try:
                os.replace(self.path, backup)
            except OSError:
                pass
            return AppConfig().validated()
        try:
            return _from_dict(AppConfig, data).validated()
        except (TypeError, ValueError):
            log.exception("config invalid; using defaults")
            return AppConfig().validated()

    def save(self, config: AppConfig | None = None) -> None:
        with self._lock:
            if config is not None:
                self.config = config
            self.config.validated()
            tmp = self.path.with_suffix(".tmp")
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp.write_text(json.dumps(asdict(self.config), indent=2), encoding="utf-8")
                os.replace(tmp, self.path)
            except OSError as exc:
                raise ConfigError(f"could not save configuration: {exc}") from exc
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(self.config)
            except Exception:
                log.exception("config listener failed")

    def on_change(self, listener: Callable[[AppConfig], None]) -> None:
        self._listeners.append(listener)


def load_dotenv(path: Path, environ: dict[str, str] | None = None) -> dict[str, str]:
    """Minimal ``.env`` loader (KEY=VALUE lines). Existing variables win."""
    environ = os.environ if environ is None else environ
    loaded: dict[str, str] = {}
    if not path.exists():
        return loaded
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if key and key not in environ:
            environ[key] = value
            loaded[key] = value
    return loaded
