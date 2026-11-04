"""Exception hierarchy.

Every error raised by TatkalMate code derives from :class:`TatkalMateError` so
the UI and the background runtime can catch application errors without
swallowing programming errors such as ``TypeError``.
"""

from __future__ import annotations


class TatkalMateError(Exception):
    """Base class for all application errors."""


class ConfigError(TatkalMateError):
    """Configuration could not be loaded or saved."""


class ValidationError(TatkalMateError):
    """User supplied data failed validation.

    ``errors`` maps a field name to a human readable message so forms can show
    the message next to the offending field.
    """

    def __init__(self, errors: dict[str, str]):
        self.errors = dict(errors)
        super().__init__("; ".join(f"{k}: {v}" for k, v in self.errors.items()))


class DatabaseError(TatkalMateError):
    """The local SQLite database failed."""


class ComplianceError(TatkalMateError):
    """An operation was refused because it would breach a compliance rule
    (for example, pointing a data provider at the IRCTC consumer website)."""


class BrowserError(TatkalMateError):
    """Base class for Brave browser integration errors."""


class BrowserNotFoundError(BrowserError):
    """Brave could not be found on this machine."""


class BrowserLaunchError(BrowserError):
    """Brave was found but could not be started."""


class ProviderError(TatkalMateError):
    """An availability provider failed. Retryable unless stated otherwise."""

    retryable = True


class ProviderTimeoutError(ProviderError):
    """The provider did not answer in time."""


class ProviderUnavailableError(ProviderError):
    """Network failure or provider outage."""


class ProviderRateLimitedError(ProviderError):
    """A request was refused locally or remotely because of rate limits."""

    def __init__(self, message: str, retry_after: float):
        super().__init__(message)
        self.retry_after = max(0.0, float(retry_after))


class ProviderNotConfiguredError(ProviderError):
    """The provider needs configuration (URL, API key) before it can be used."""

    retryable = False


class ProviderResponseError(ProviderError):
    """The provider answered with data that could not be understood."""
