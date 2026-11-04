"""Health checks and network status.

The network probe opens a TCP connection to a neutral host (default
1.1.1.1:443, configurable). It never contacts IRCTC.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import tempfile
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING, Callable

from core.clock import Clock
from core.config import NetworkConfig
from core.event_bus import EventBus, EventType
from core.paths import AppPaths
from core.secure_codec import SecureCodec
from core.version import __version__
from database.connection import Database
from providers.base_provider import AvailabilityProvider, ProviderHealth

if TYPE_CHECKING:
    from core.async_runtime import AsyncRuntime
    from services.brave_service import BraveService

log = logging.getLogger(__name__)

MIN_FREE_BYTES = 100 * 1024 * 1024


class CheckStatus(str, Enum):
    OK = "OK"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class HealthCheck:
    name: str
    status: CheckStatus
    detail: str


@dataclass
class HealthReport:
    generated_at: datetime
    checks: list[HealthCheck] = field(default_factory=list)

    @property
    def overall(self) -> CheckStatus:
        statuses = {c.status for c in self.checks}
        if CheckStatus.FAIL in statuses:
            return CheckStatus.FAIL
        if CheckStatus.WARN in statuses:
            return CheckStatus.WARN
        return CheckStatus.OK


class HealthService:
    def __init__(
        self,
        db: Database,
        paths: AppPaths,
        codec: SecureCodec,
        provider: Callable[[], AvailabilityProvider],
        network_config: Callable[[], NetworkConfig],
        bus: EventBus,
        clock: Clock,
        runtime: "AsyncRuntime | None" = None,
        brave: "BraveService | None" = None,
        connector: Callable[..., socket.socket] = socket.create_connection,
    ):
        self._db = db
        self._paths = paths
        self._codec = codec
        self._provider = provider
        self._network_config = network_config
        self._bus = bus
        self._clock = clock
        self._runtime = runtime
        self._brave = brave
        self._connector = connector
        self.network_online: bool | None = None
        self.provider_health: ProviderHealth | None = None
        self.provider_message: str = ""
        self.latest: HealthReport | None = None

    def probe_network(self) -> bool:
        cfg = self._network_config()
        try:
            sock = self._connector((cfg.probe_host, cfg.probe_port), timeout=3)
            sock.close()
            return True
        except OSError:
            return False

    async def run_checks(self) -> HealthReport:
        report = HealthReport(self._clock.now_utc())
        add = report.checks.append
        add(HealthCheck("Version", CheckStatus.OK, __version__))

        add(HealthCheck("Database", CheckStatus.OK, self._paths.database_file.name)
            if self._db.ping() else HealthCheck("Database", CheckStatus.FAIL, "SQLite is not responding"))

        add(self._check_writable())

        try:
            free = shutil.disk_usage(self._paths.root).free
            add(HealthCheck("Disk space", CheckStatus.OK if free > MIN_FREE_BYTES else CheckStatus.WARN,
                            f"{free / 1024**3:.1f} GB free"))
        except OSError as exc:
            add(HealthCheck("Disk space", CheckStatus.WARN, str(exc)))

        online = await asyncio.to_thread(self.probe_network)
        if self.network_online is not None and online != self.network_online:
            self._bus.publish(EventType.NETWORK_CHANGED, online=online)
        self.network_online = online
        add(HealthCheck("Network", CheckStatus.OK if online else CheckStatus.FAIL,
                        "Internet reachable" if online else "No internet connection"))

        provider = self._provider()
        try:
            status = await asyncio.wait_for(provider.check_status(), timeout=10)
            self.provider_health, self.provider_message = status.health, status.message
        except Exception as exc:
            self.provider_health, self.provider_message = ProviderHealth.DOWN, str(exc)
        level = {ProviderHealth.OK: CheckStatus.OK, ProviderHealth.DEGRADED: CheckStatus.WARN,
                 ProviderHealth.NOT_CONFIGURED: CheckStatus.WARN, ProviderHealth.DOWN: CheckStatus.FAIL}
        detail = f"{provider.info.name}: {self.provider_message}"
        if not provider.info.is_real_source:
            detail += " (simulated, not a real data source)"
        add(HealthCheck("Availability provider",
                        CheckStatus.WARN if not provider.info.is_real_source else level[self.provider_health],
                        detail))

        if self._runtime is not None:
            add(HealthCheck("Background tasks", CheckStatus.OK if self._runtime.is_running else CheckStatus.FAIL,
                            ", ".join(self._runtime.task_names()) or "none"))
        if self._brave is not None:
            install = self._brave.installation
            add(HealthCheck("Brave browser", CheckStatus.OK if install else CheckStatus.WARN,
                            f"{install.path} ({install.source})" if install else "not found"))
        add(HealthCheck("Personal data protection",
                        CheckStatus.OK if self._codec.mode == "dpapi" else CheckStatus.WARN,
                        "Windows DPAPI encryption" if self._codec.mode == "dpapi"
                        else "Owner-only file permissions (DPAPI is Windows-only)"))
        self.latest = report
        self._bus.publish(EventType.HEALTH_UPDATED, report=report)
        return report

    def _check_writable(self) -> HealthCheck:
        try:
            fd, name = tempfile.mkstemp(dir=self._paths.log_dir, prefix=".probe")
            os.close(fd)
            os.remove(name)
            return HealthCheck("Data folder", CheckStatus.OK, str(self._paths.root))
        except OSError as exc:
            return HealthCheck("Data folder", CheckStatus.FAIL, f"not writable: {exc}")

    async def run_loop(self) -> None:
        while True:
            try:
                await self.run_checks()
            except Exception:
                log.exception("health check failed")
            await asyncio.sleep(self._network_config().probe_interval_seconds)
