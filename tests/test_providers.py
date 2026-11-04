import asyncio
import json
from datetime import date

import pytest

from core.exceptions import ComplianceError, ProviderNotConfiguredError, ProviderRateLimitedError, ProviderResponseError
from models.availability import AvailabilityQuery, AvailabilityStatus, Freshness
from models.journey import TatkalCategory, TravelClass
from providers.authorized_provider import AuthorizedAvailabilityProvider, check_endpoint_allowed
from providers.base_provider import ProviderHealth
from tests.helpers import fake_clock

Q = AvailabilityQuery("12951", date(2026, 11, 20), TravelClass.AC_3_TIER, TatkalCategory.TATKAL)


@pytest.mark.parametrize("url", ["https://www.irctc.co.in/eticketing", "https://irctc.co.in", "https://enquiry.indianrail.gov.in/x",
                                 "http://api.example.com", "https://"])
def test_blocked_endpoints(url):
    with pytest.raises(ComplianceError):
        check_endpoint_allowed(url)


def test_allowed_endpoint():
    assert check_endpoint_allowed("https://api.example.com/v1") == "api.example.com"
    assert check_endpoint_allowed("https://notirctc.co.in.example.com") == "notirctc.co.in.example.com"


def test_unconfigured_provider_reports_and_refuses():
    p = AuthorizedAvailabilityProvider(env={})
    assert asyncio.run(p.check_status()).health is ProviderHealth.NOT_CONFIGURED
    with pytest.raises(ProviderNotConfiguredError):
        asyncio.run(p.fetch_availability(Q))


def test_irctc_url_is_refused_at_configuration():
    p = AuthorizedAvailabilityProvider(env={"TATKALMATE_PROVIDER_URL": "https://www.irctc.co.in/api",
                                            "TATKALMATE_PROVIDER_API_KEY": "k"})
    assert not p.configured


def test_min_interval_floor():
    p = AuthorizedAvailabilityProvider(env={"TATKALMATE_PROVIDER_MIN_INTERVAL": "1"})
    assert p.info.min_request_interval_seconds == 60


def _provider(monkeypatch, status, body, headers=None):
    clock = fake_clock()
    p = AuthorizedAvailabilityProvider(env={"TATKALMATE_PROVIDER_URL": "https://api.example.com/v1",
                                            "TATKALMATE_PROVIDER_API_KEY": "secret"}, clock=clock)
    sent = {}

    def fake_send(req):
        sent["url"] = req.full_url
        sent["auth"] = req.get_header("Authorization")
        return status, headers or {}, json.dumps(body).encode() if not isinstance(body, bytes) else body

    monkeypatch.setattr(p, "_send", fake_send)
    return p, clock, sent


def test_parses_confirmed_live_result(monkeypatch):
    p, clock, sent = _provider(monkeypatch, 200, {"status": "available", "seats": 42, "as_of": "2026-11-19T09:00:00+05:30",
                                                  "cached": False, "confirmed": True, "display": "AVAILABLE-0042"})
    r = asyncio.run(p.fetch_availability(Q))
    assert "train=12951" in sent["url"] and "quota=TQ" in sent["url"] and sent["auth"] == "Bearer secret"
    assert r.status is AvailabilityStatus.AVAILABLE and r.seats == 42 and not r.simulated
    assert r.freshness(clock.now_utc(), 900) is Freshness.VERIFIED


def test_missing_flags_are_treated_pessimistically(monkeypatch):
    p, clock, _ = _provider(monkeypatch, 200, {"status": "AVAILABLE", "seats": 3})
    r = asyncio.run(p.fetch_availability(Q))
    assert r.is_cached and not r.confirmed_by_source
    assert r.freshness(clock.now_utc(), 900) is not Freshness.VERIFIED


def test_unknown_status_is_unknown(monkeypatch):
    p, _, _ = _provider(monkeypatch, 200, {"status": "MAYBE"})
    assert asyncio.run(p.fetch_availability(Q)).status is AvailabilityStatus.UNKNOWN


@pytest.mark.parametrize("body", [{"status": "AVAILABLE", "seats": -1}, {"status": "AVAILABLE", "as_of": "yesterday"},
                                  [1, 2], b"not json"])
def test_bad_responses(monkeypatch, body):
    p, _, _ = _provider(monkeypatch, 200, body)
    with pytest.raises(ProviderResponseError):
        asyncio.run(p.fetch_availability(Q))


def test_http_429_honours_retry_after(monkeypatch):
    p, _, _ = _provider(monkeypatch, 429, {}, {"Retry-After": "120"})
    with pytest.raises(ProviderRateLimitedError) as e:
        asyncio.run(p.fetch_availability(Q))
    assert e.value.retry_after == 120


def test_http_401_is_not_configured(monkeypatch):
    p, _, _ = _provider(monkeypatch, 401, {})
    with pytest.raises(ProviderNotConfiguredError):
        asyncio.run(p.fetch_availability(Q))
