"""Journey domain model."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date, timedelta
from enum import Enum

from core.exceptions import ValidationError

MAX_BOARDING_DAY_OFFSET = 4

_TRAIN_RE = re.compile(r"^\d{5}$")
_STATION_RE = re.compile(r"^[A-Z]{1,5}$")


class TravelClass(str, Enum):
    FIRST_AC = "1A"
    AC_2_TIER = "2A"
    AC_3_TIER = "3A"
    AC_3_ECONOMY = "3E"
    AC_CHAIR_CAR = "CC"
    EXECUTIVE_CHAIR_CAR = "EC"
    SLEEPER = "SL"
    SECOND_SITTING = "2S"
    FIRST_CLASS = "FC"

    @property
    def is_ac(self) -> bool:
        return self in _AC_CLASSES

    @property
    def tatkal_eligible(self) -> bool:
        # Tatkal quota is not offered in First AC.
        return self is not TravelClass.FIRST_AC

    @property
    def label(self) -> str:
        return _CLASS_LABELS[self]


_AC_CLASSES = {
    TravelClass.FIRST_AC,
    TravelClass.AC_2_TIER,
    TravelClass.AC_3_TIER,
    TravelClass.AC_3_ECONOMY,
    TravelClass.AC_CHAIR_CAR,
    TravelClass.EXECUTIVE_CHAIR_CAR,
}

_CLASS_LABELS = {
    TravelClass.FIRST_AC: "1A - First AC",
    TravelClass.AC_2_TIER: "2A - AC 2 Tier",
    TravelClass.AC_3_TIER: "3A - AC 3 Tier",
    TravelClass.AC_3_ECONOMY: "3E - AC 3 Economy",
    TravelClass.AC_CHAIR_CAR: "CC - AC Chair Car",
    TravelClass.EXECUTIVE_CHAIR_CAR: "EC - Executive Chair Car",
    TravelClass.SLEEPER: "SL - Sleeper",
    TravelClass.SECOND_SITTING: "2S - Second Sitting",
    TravelClass.FIRST_CLASS: "FC - First Class",
}


class TatkalCategory(str, Enum):
    TATKAL = "TQ"
    PREMIUM_TATKAL = "PT"

    @property
    def label(self) -> str:
        return "Tatkal" if self is TatkalCategory.TATKAL else "Premium Tatkal"


@dataclass(frozen=True)
class Journey:
    """A journey the user is preparing to book.

    ``journey_date`` is the date the passenger boards at ``from_station``.
    ``boarding_day_offset`` is how many calendar days after leaving its
    originating station the train reaches ``from_station`` (0 when boarding at
    the origin, 1 when the train left the origin the previous day, ...). The
    Tatkal opening date is computed from the origin departure date, not from
    the boarding date.
    """

    train_number: str
    from_station: str
    to_station: str
    journey_date: date
    travel_class: TravelClass
    category: TatkalCategory = TatkalCategory.TATKAL
    train_name: str = ""
    boarding_day_offset: int = 0
    id: int | None = None

    @property
    def origin_departure_date(self) -> date:
        return self.journey_date - timedelta(days=self.boarding_day_offset)

    @property
    def context_key(self) -> str:
        """Stable identity used to invalidate manual confirmations when the
        journey changes."""
        return "|".join(
            [
                self.train_number,
                self.from_station,
                self.to_station,
                self.journey_date.isoformat(),
                self.travel_class.value,
                self.category.value,
                str(self.boarding_day_offset),
            ]
        )

    def normalized(self) -> "Journey":
        return replace(
            self,
            train_number=self.train_number.strip(),
            from_station=self.from_station.strip().upper(),
            to_station=self.to_station.strip().upper(),
            train_name=" ".join(self.train_name.split()),
        )

    def validate(self) -> "Journey":
        """Return a normalized copy or raise :class:`ValidationError`."""
        j = self.normalized()
        errors: dict[str, str] = {}
        if not _TRAIN_RE.match(j.train_number):
            errors["train_number"] = "Train number must be exactly 5 digits."
        if not _STATION_RE.match(j.from_station):
            errors["from_station"] = "Station code must be 1-5 letters (e.g. ST, NDLS)."
        if not _STATION_RE.match(j.to_station):
            errors["to_station"] = "Station code must be 1-5 letters (e.g. BCT, HWH)."
        if not errors.get("from_station") and j.from_station == j.to_station:
            errors["to_station"] = "Destination must differ from the boarding station."
        if len(j.train_name) > 60:
            errors["train_name"] = "Train name is limited to 60 characters."
        if not j.travel_class.tatkal_eligible:
            errors["travel_class"] = f"{j.travel_class.label} has no Tatkal quota."
        if not 0 <= j.boarding_day_offset <= MAX_BOARDING_DAY_OFFSET:
            errors["boarding_day_offset"] = (
                f"Boarding day offset must be between 0 and {MAX_BOARDING_DAY_OFFSET}."
            )
        if errors:
            raise ValidationError(errors)
        return j
