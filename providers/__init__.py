"""Provider factory."""

from __future__ import annotations

from core.clock import Clock
from providers.authorized_provider import AuthorizedAvailabilityProvider
from providers.base_provider import AvailabilityProvider
from providers.mock_provider import MockAvailabilityProvider


def create_provider(provider_id: str, clock: Clock, timeout: float = 15.0) -> AvailabilityProvider:
    if provider_id == "authorized":
        return AuthorizedAvailabilityProvider(clock=clock, timeout=timeout)
    return MockAvailabilityProvider(clock=clock)
