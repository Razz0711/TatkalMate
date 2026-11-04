from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class CheckSource(str, Enum):
    MANUAL = "MANUAL"  # the user said so; TatkalMate cannot verify it
    AUTO_DETECTED = "AUTO_DETECTED"  # observed locally (e.g. Brave process running)
    DERIVED = "DERIVED"  # computed from app data (e.g. a valid journey is saved)


class CheckState(str, Enum):
    DONE = "DONE"
    PENDING = "PENDING"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class ReadinessItem:
    id: str
    label: str
    state: CheckState
    source: CheckSource
    detail: str = ""
    confirmed_at: datetime | None = None
    user_confirmable: bool = False

    @property
    def done(self) -> bool:
        return self.state is CheckState.DONE
