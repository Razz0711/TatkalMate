"""IRCTC eWallet readiness.

TatkalMate never reads the wallet balance or touches payment. The user checks
the balance on the official IRCTC site and marks it here; the mark expires
after ``CHECK_VALIDITY_HOURS`` because balances change.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from core.clock import Clock
from core.config import IRCTC_HOME_URL
from core.event_bus import EventBus, EventType
from database.repositories.state_repository import StateRepository

CHECK_ITEM = "wallet_checked"
FARE_KEY = "wallet.estimated_fare"
CHECK_VALIDITY_HOURS = 12
BUFFER_RATIO = 0.10  # cover fare changes and convenience/gateway charges
ROUND_TO = 50

PAYMENT_STEPS = [
    "Before opening time, sign in on the official IRCTC website and open My Account → IRCTC eWallet "
    "to see your balance. Top up there if needed. Top-ups can take a few minutes to show.",
    "When Tatkal opens, search your train, choose the class and the Tatkal quota, and fill in the passenger "
    "details yourself (IRCTC's master list can speed this up).",
    "Solve the CAPTCHA yourself. TatkalMate does not read or solve CAPTCHAs.",
    "On the payment page choose 'IRCTC eWallet' and confirm the amount.",
    "IRCTC asks you to authorise the payment (for example with your eWallet transaction password or an OTP, "
    "depending on your account). Enter it only on the IRCTC page. TatkalMate never asks for, sees or stores it.",
    "Wait for the booking confirmation and PNR from IRCTC. Do not refresh or press Back during payment.",
]


class WalletState(str, Enum):
    NOT_CHECKED = "NOT_CHECKED"
    CHECKED = "CHECKED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class WalletStatus:
    state: WalletState
    checked_at: datetime | None
    estimated_fare: float | None
    recommended_balance: int | None
    message: str


def recommended_balance(estimated_fare: float | None) -> int | None:
    if estimated_fare is None or estimated_fare <= 0:
        return None
    return int(math.ceil(estimated_fare * (1 + BUFFER_RATIO) / ROUND_TO) * ROUND_TO)


class WalletReadinessService:
    official_url = IRCTC_HOME_URL
    payment_steps = PAYMENT_STEPS

    def __init__(self, state: StateRepository, bus: EventBus, clock: Clock):
        self._state = state
        self._bus = bus
        self._clock = clock

    def mark_checked(self) -> None:
        self._state.confirm(CHECK_ITEM, "wallet", self._clock.now_utc())
        self._bus.publish(EventType.WALLET_CHANGED)
        self._bus.publish(EventType.READINESS_CHANGED)

    def clear(self) -> None:
        self._state.unconfirm(CHECK_ITEM)
        self._bus.publish(EventType.WALLET_CHANGED)
        self._bus.publish(EventType.READINESS_CHANGED)

    def set_estimated_fare(self, amount: float | None) -> None:
        if amount is None or amount <= 0:
            self._state.delete(FARE_KEY)
        else:
            if amount > 1_000_000:
                raise ValueError("estimated fare looks wrong")
            self._state.set(FARE_KEY, f"{float(amount):.2f}")
        self._bus.publish(EventType.WALLET_CHANGED)

    def estimated_fare(self) -> float | None:
        raw = self._state.get(FARE_KEY)
        try:
            return float(raw) if raw else None
        except ValueError:
            return None

    def status(self) -> WalletStatus:
        fare = self.estimated_fare()
        rec = recommended_balance(fare)
        conf = self._state.confirmation(CHECK_ITEM)
        balance_hint = f" Keep at least ₹{rec:,} in the eWallet." if rec else ""
        if conf is None:
            return WalletStatus(WalletState.NOT_CHECKED, None, fare, rec,
                                "Not checked yet. Check your balance on the IRCTC site." + balance_hint)
        checked_at = conf[1]
        if self._clock.now_utc() - checked_at > timedelta(hours=CHECK_VALIDITY_HOURS):
            return WalletStatus(WalletState.EXPIRED, checked_at, fare, rec,
                                f"Last checked over {CHECK_VALIDITY_HOURS} h ago. Please check again." + balance_hint)
        return WalletStatus(WalletState.CHECKED, checked_at, fare, rec,
                            "You marked the balance as checked. Not verified by TatkalMate." + balance_hint)
