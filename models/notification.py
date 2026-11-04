from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Severity(str, Enum):
    INFO = "INFO"
    SUCCESS = "SUCCESS"
    WARNING = "WARNING"
    CRITICAL = "CRITICAL"


class NotificationKind(str, Enum):
    AVAILABILITY = "AVAILABILITY"
    TATKAL_OPENING = "TATKAL_OPENING"
    BROWSER = "BROWSER"
    MONITOR = "MONITOR"
    NETWORK = "NETWORK"
    WALLET = "WALLET"
    CLOCK = "CLOCK"
    SYSTEM = "SYSTEM"


@dataclass(frozen=True)
class Notification:
    kind: NotificationKind
    severity: Severity
    title: str
    body: str
    dedupe_key: str
    created_at: datetime
    read: bool = False
    id: int | None = None
