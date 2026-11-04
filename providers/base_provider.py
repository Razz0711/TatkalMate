"""Availability provider abstraction.

A provider turns an :class:`AvailabilityQuery` into an
:class:`AvailabilityResult`. Providers must:

* report honestly whether data is cached and whether the source confirmed it;
* declare the minimum interval between requests they need
  (``ProviderInfo.min_request_interval_seconds``); the monitoring service
  enforces ``max(declared, HARD_MIN_POLL_SECONDS)``;
* raise the ``Provider*Error`` exceptions from ``core.exceptions`` on failure
  instead of returning made-up data.

Providers must never scrape the IRCTC consumer website, call undocumented
endpoints, or work around CAPTCHAs or anti-bot protection.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from enum import Enum

from models.availability import AvailabilityQuery, AvailabilityResult


class ProviderHealth(str, Enum):
    OK = "OK"
    DEGRADED = "DEGRADED"
    DOWN = "DOWN"
    NOT_CONFIGURED = "NOT_CONFIGURED"


@dataclass(frozen=True)
class ProviderInfo:
    id: str
    name: str
    description: str
    is_real_source: bool
    min_request_interval_seconds: float


@dataclass(frozen=True)
class ProviderStatus:
    health: ProviderHealth
    message: str


class AvailabilityProvider(ABC):
    info: ProviderInfo

    @abstractmethod
    async def fetch_availability(self, query: AvailabilityQuery) -> AvailabilityResult:
        """Fetch availability once. Must raise a ``ProviderError`` on failure."""

    @abstractmethod
    async def check_status(self) -> ProviderStatus:
        """Cheap readiness check. Must not query availability."""

    async def close(self) -> None:  # pragma: no cover - default no-op
        return None
