"""Passenger profile model.

Only booking-form data is stored. Passwords, OTPs, ID document numbers and
payment details are never part of this model.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from enum import Enum

from core.exceptions import ValidationError

# IRCTC limits a booking (PNR) under Tatkal quota to 4 passengers.
MAX_TATKAL_PASSENGERS = 4
NAME_MIN, NAME_MAX = 3, 16  # IRCTC passenger-name field limits
AGE_MIN, AGE_MAX = 5, 125  # children under 5 do not need a ticket

_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z .]*$")


class Gender(str, Enum):
    MALE = "M"
    FEMALE = "F"
    TRANSGENDER = "T"

    @property
    def label(self) -> str:
        return {"M": "Male", "F": "Female", "T": "Transgender"}[self.value]


class BerthPreference(str, Enum):
    NO_PREFERENCE = ""
    LOWER = "LB"
    MIDDLE = "MB"
    UPPER = "UB"
    SIDE_LOWER = "SL"
    SIDE_UPPER = "SU"
    WINDOW = "WS"

    @property
    def label(self) -> str:
        return {
            "": "No preference",
            "LB": "Lower",
            "MB": "Middle",
            "UB": "Upper",
            "SL": "Side lower",
            "SU": "Side upper",
            "WS": "Window (chair car)",
        }[self.value]


class FoodPreference(str, Enum):
    NONE = ""
    VEG = "V"
    NON_VEG = "N"
    NO_FOOD = "D"

    @property
    def label(self) -> str:
        return {"": "Not set", "V": "Veg", "N": "Non-veg", "D": "No food"}[self.value]


@dataclass(frozen=True)
class Passenger:
    name: str
    age: int
    gender: Gender
    berth_preference: BerthPreference = BerthPreference.NO_PREFERENCE
    food_preference: FoodPreference = FoodPreference.NONE
    include_in_booking: bool = True
    id: int | None = None

    def normalized(self) -> "Passenger":
        return replace(self, name=" ".join(self.name.split()))

    def validate(self) -> "Passenger":
        p = self.normalized()
        errors: dict[str, str] = {}
        if not NAME_MIN <= len(p.name) <= NAME_MAX:
            errors["name"] = f"Name must be {NAME_MIN}-{NAME_MAX} characters (IRCTC limit)."
        elif not _NAME_RE.match(p.name):
            errors["name"] = "Name may contain only letters, spaces and dots."
        if isinstance(p.age, bool) or not isinstance(p.age, int):
            errors["age"] = "Age must be a whole number."
        elif not AGE_MIN <= p.age <= AGE_MAX:
            errors["age"] = f"Age must be between {AGE_MIN} and {AGE_MAX}."
        if not isinstance(p.gender, Gender):
            errors["gender"] = "Choose a gender."
        if errors:
            raise ValidationError(errors)
        return p

    @property
    def masked_name(self) -> str:
        """Initials only; safe for logs and notifications."""
        return " ".join(part[0] + "." for part in self.name.split() if part)
