"""Display helpers shared by views."""

from __future__ import annotations

from datetime import datetime

from core.scheduler import IST
from models.availability import AvailabilityResult, AvailabilityStatus, Freshness
from models.readiness import CheckSource, CheckState


def ist(dt: datetime | None, fmt: str = "%d %b %Y, %H:%M:%S") -> str:
    if dt is None:
        return "—"
    return dt.astimezone(IST).strftime(fmt) + " IST"


def ago(dt: datetime | None, now: datetime) -> str:
    if dt is None:
        return "never"
    s = int((now - dt).total_seconds())
    if s < 0:
        return "just now"
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h {s % 3600 // 60}m ago"
    return f"{s // 86400}d ago"


FRESHNESS = {
    Freshness.VERIFIED: ("Verified", "ok"),
    Freshness.CACHED: ("Cached, not verified", "warn"),
    Freshness.STALE: ("Stale", "bad"),
    Freshness.UNCONFIRMED: ("Unconfirmed", "warn"),
}

STATUS_TONE = {
    AvailabilityStatus.AVAILABLE: "ok",
    AvailabilityStatus.RAC: "warn",
    AvailabilityStatus.WAITLIST: "warn",
    AvailabilityStatus.REGRET: "bad",
    AvailabilityStatus.NOT_AVAILABLE: "bad",
    AvailabilityStatus.UNKNOWN: "",
}

CHECK_STATE = {
    CheckState.DONE: ("Done", "ok"),
    CheckState.PENDING: ("Pending", ""),
    CheckState.EXPIRED: ("Re-check", "warn"),
}

CHECK_SOURCE = {
    CheckSource.MANUAL: "Confirmed by you",
    CheckSource.AUTO_DETECTED: "Auto-detected",
    CheckSource.DERIVED: "From app data",
}


_SIM = {Freshness.VERIFIED: "fresh", Freshness.CACHED: "cached", Freshness.STALE: "stale",
        Freshness.UNCONFIRMED: "unconfirmed"}


def freshness_label(result: AvailabilityResult, at: datetime, stale_after: float) -> tuple[str, str]:
    """Label and tone for a result. Simulated data is never shown as verified or green."""
    fr = result.freshness(at, stale_after)
    if result.simulated:
        return f"Simulated · {_SIM[fr]}", "bad" if fr is Freshness.STALE else "warn"
    return FRESHNESS[fr]
