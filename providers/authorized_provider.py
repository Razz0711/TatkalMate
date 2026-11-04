"""Adapter for an *authorized* availability data provider.

NOT CONNECTED. TatkalMate does not ship with a real-time availability source,
because IRCTC does not offer a public API for individual users and scraping
the IRCTC website or calling its undocumented endpoints is not permitted.

This adapter exists so a licensed source (for example an IRCTC-authorized B2B
partner API or a commercial data provider whose terms allow this use) can be
plugged in with configuration only. It speaks a small, documented JSON
contract over HTTPS. If your provider's API differs, write a subclass that
overrides ``_build_request`` and ``_parse``.

Configuration (environment variables or a local ``.env`` file):

* ``TATKALMATE_PROVIDER_URL``: HTTPS base URL, e.g. ``https://api.example.com/v1``
* ``TATKALMATE_PROVIDER_API_KEY``: API key, sent as ``Authorization: Bearer``
* ``TATKALMATE_PROVIDER_NAME``: display name (optional)
* ``TATKALMATE_PROVIDER_MIN_INTERVAL``: seconds between requests the provider
  allows (optional, default 120, never below the 60 s floor)

Expected request: ``GET {base}/availability?train=12951&date=2026-11-20&class=3A&quota=TQ&from=ST&to=BCT``

Expected JSON response::

    {"status": "AVAILABLE" | "RAC" | "WAITLIST" | "REGRET" | "NOT_AVAILABLE",
     "seats": 42,                       // optional integer
     "display": "AVAILABLE-0042",       // optional raw text
     "as_of": "2026-11-19T10:00:05+05:30",  // when the source observed it
     "cached": false,                   // true if served from a cache
     "confirmed": true}                 // source vouches for the figure
"""

from __future__ import annotations

import asyncio
import json
import os
import socket
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

from core.clock import Clock, SystemClock
from core.config import HARD_MIN_POLL_SECONDS
from core.exceptions import (
    ComplianceError,
    ProviderNotConfiguredError,
    ProviderRateLimitedError,
    ProviderResponseError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from models.availability import AvailabilityQuery, AvailabilityResult, AvailabilityStatus
from providers.base_provider import AvailabilityProvider, ProviderHealth, ProviderInfo, ProviderStatus

# Hosts this adapter refuses to talk to. Their consumer websites are not an
# authorized data feed for third-party tools.
BLOCKED_HOST_SUFFIXES = ("irctc.co.in", "indianrail.gov.in", "indianrailways.gov.in")


def check_endpoint_allowed(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https":
        raise ComplianceError("provider URL must use https")
    host = (parsed.hostname or "").lower().rstrip(".")
    if not host:
        raise ComplianceError("provider URL has no host")
    for suffix in BLOCKED_HOST_SUFFIXES:
        if host == suffix or host.endswith("." + suffix):
            raise ComplianceError(
                f"{host} is an Indian Railways/IRCTC website, not an authorized data API for this tool"
            )
    return host


class AuthorizedAvailabilityProvider(AvailabilityProvider):
    def __init__(self, env: dict[str, str] | None = None, clock: Clock | None = None, timeout: float = 15.0):
        env = dict(os.environ if env is None else env)
        self._clock = clock or SystemClock()
        self._timeout = timeout
        self._base_url = env.get("TATKALMATE_PROVIDER_URL", "").strip().rstrip("/")
        self._api_key = env.get("TATKALMATE_PROVIDER_API_KEY", "").strip()
        try:
            min_interval = float(env.get("TATKALMATE_PROVIDER_MIN_INTERVAL", "120"))
        except ValueError:
            min_interval = 120.0
        self._config_error: str | None = None
        if not self._base_url or not self._api_key:
            self._config_error = "TATKALMATE_PROVIDER_URL and TATKALMATE_PROVIDER_API_KEY are not set"
        else:
            try:
                check_endpoint_allowed(self._base_url)
            except ComplianceError as exc:
                self._config_error = str(exc)
        name = env.get("TATKALMATE_PROVIDER_NAME", "").strip() or "Authorized provider"
        self.info = ProviderInfo(
            id="authorized",
            name=name,
            description="Licensed availability API configured via environment variables.",
            is_real_source=True,
            min_request_interval_seconds=max(HARD_MIN_POLL_SECONDS, min_interval),
        )

    @property
    def configured(self) -> bool:
        return self._config_error is None

    async def check_status(self) -> ProviderStatus:
        if self._config_error:
            return ProviderStatus(ProviderHealth.NOT_CONFIGURED, self._config_error)
        return ProviderStatus(ProviderHealth.OK, f"Configured for {urllib.parse.urlparse(self._base_url).hostname}")

    async def fetch_availability(self, query: AvailabilityQuery) -> AvailabilityResult:
        if self._config_error:
            raise ProviderNotConfiguredError(self._config_error)
        request = self._build_request(query)
        status, headers, body = await asyncio.to_thread(self._send, request)
        if status == 429:
            retry_after = _parse_retry_after(headers.get("Retry-After"))
            raise ProviderRateLimitedError("provider rate limit reached", retry_after)
        if status in (401, 403):
            raise ProviderNotConfiguredError(f"provider rejected credentials (HTTP {status})")
        if status >= 500:
            raise ProviderUnavailableError(f"provider error HTTP {status}")
        if status != 200:
            raise ProviderResponseError(f"unexpected HTTP {status}")
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ProviderResponseError("response is not valid JSON") from exc
        return self._parse(query, payload)

    def _build_request(self, query: AvailabilityQuery) -> urllib.request.Request:
        params = {
            "train": query.train_number,
            "date": query.journey_date.isoformat(),
            "class": query.travel_class.value,
            "quota": query.category.value,
        }
        if query.from_station:
            params["from"] = query.from_station
        if query.to_station:
            params["to"] = query.to_station
        url = f"{self._base_url}/availability?{urllib.parse.urlencode(params)}"
        check_endpoint_allowed(url)
        return urllib.request.Request(
            url,
            headers={"Authorization": f"Bearer {self._api_key}", "Accept": "application/json",
                     "User-Agent": "TatkalMate/0.1"},
            method="GET",
        )

    def _send(self, request: urllib.request.Request) -> tuple[int, dict[str, str], bytes]:
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as resp:  # noqa: S310 - https enforced
                return resp.status, dict(resp.headers), resp.read(1_000_000)
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers or {}), b""
        except (socket.timeout, TimeoutError) as exc:
            raise ProviderTimeoutError("provider timed out") from exc
        except urllib.error.URLError as exc:
            if isinstance(exc.reason, (socket.timeout, TimeoutError)):
                raise ProviderTimeoutError("provider timed out") from exc
            raise ProviderUnavailableError(f"network error: {exc.reason}") from exc
        except OSError as exc:
            raise ProviderUnavailableError(f"network error: {exc}") from exc

    def _parse(self, query: AvailabilityQuery, payload: Any) -> AvailabilityResult:
        if not isinstance(payload, dict):
            raise ProviderResponseError("response must be a JSON object")
        try:
            status = AvailabilityStatus(str(payload.get("status", "")).upper())
        except ValueError:
            status = AvailabilityStatus.UNKNOWN
        seats = payload.get("seats")
        if seats is not None and (isinstance(seats, bool) or not isinstance(seats, int) or seats < 0):
            raise ProviderResponseError("'seats' must be a non-negative integer")
        as_of = None
        if payload.get("as_of"):
            try:
                as_of = datetime.fromisoformat(str(payload["as_of"]))
                if as_of.tzinfo is None:
                    raise ValueError
                as_of = as_of.astimezone(timezone.utc)
            except ValueError as exc:
                raise ProviderResponseError("'as_of' must be an ISO-8601 timestamp with offset") from exc
        cached = payload.get("cached")
        confirmed = payload.get("confirmed")
        return AvailabilityResult(
            query=query,
            status=status,
            seats=seats,
            source=self.info.name,
            fetched_at=self._clock.now_utc(),
            source_timestamp=as_of,
            # Missing flags are treated pessimistically.
            is_cached=cached is not False,
            confirmed_by_source=confirmed is True and as_of is not None,
            simulated=False,
            raw_text=str(payload.get("display", ""))[:64],
        )


def _parse_retry_after(value: str | None) -> float:
    try:
        return max(1.0, float(value)) if value else 300.0
    except ValueError:
        return 300.0
