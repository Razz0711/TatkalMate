"""IST synchronisation via SNTP (RFC 4330).

The PC clock can be seconds off. ``OffsetClock`` adds the measured offset to
the system clock, so the countdown follows network time even when Windows time
sync is off or drifting. Monotonic time is left untouched.
"""

from __future__ import annotations

import socket
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Callable

from core.clock import Clock

NTP_EPOCH_DELTA = 2208988800  # seconds between 1900-01-01 and 1970-01-01
DEFAULT_SERVERS = ("time.google.com", "time.windows.com", "pool.ntp.org", "time.cloudflare.com")
MAX_SANE_OFFSET = 24 * 3600


class OffsetClock:
    """System clock corrected by an offset (seconds) measured against NTP."""

    def __init__(self, base: Clock):
        self._base = base
        self._offset = 0.0
        self._lock = threading.Lock()

    @property
    def offset(self) -> float:
        with self._lock:
            return self._offset

    def set_offset(self, seconds: float) -> None:
        with self._lock:
            self._offset = float(seconds)

    def now_utc(self) -> datetime:
        return self._base.now_utc() + timedelta(seconds=self.offset)

    def raw_now_utc(self) -> datetime:
        return self._base.now_utc()

    def monotonic(self) -> float:
        return self._base.monotonic()


@dataclass(frozen=True)
class NtpSample:
    server: str
    offset: float  # seconds to ADD to the local clock
    delay: float  # round-trip delay in seconds
    measured_at: datetime


class NtpError(Exception):
    pass


Transport = Callable[[str, bytes, float], bytes]


def udp_transport(host: str, packet: bytes, timeout: float) -> bytes:
    addr = socket.getaddrinfo(host, 123, type=socket.SOCK_DGRAM)[0]
    with socket.socket(addr[0], socket.SOCK_DGRAM) as sock:
        sock.settimeout(timeout)
        sock.sendto(packet, addr[4])
        data, _ = sock.recvfrom(512)
        return data


def _ts(seconds: int, fraction: int) -> float:
    return seconds - NTP_EPOCH_DELTA + fraction / 2**32


def sntp_query(server: str, transport: Transport = udp_transport, wall: Callable[[], float] = time.time,
               timeout: float = 2.0) -> NtpSample:
    request = b"\x23" + 47 * b"\0"  # LI=0, VN=4, Mode=3 (client)
    t1 = wall()
    try:
        data = transport(server, request, timeout)
    except OSError as exc:
        raise NtpError(f"{server}: {exc}") from exc
    t4 = wall()
    if len(data) < 48:
        raise NtpError(f"{server}: short response")
    words = struct.unpack("!12I", data[:48])
    li, mode, stratum = data[0] >> 6, data[0] & 0x7, data[1]
    if mode != 4 or li == 3 or not 1 <= stratum <= 15:
        raise NtpError(f"{server}: server not synchronised (li={li}, mode={mode}, stratum={stratum})")
    t2 = _ts(words[8], words[9])
    t3 = _ts(words[10], words[11])
    offset = ((t2 - t1) + (t3 - t4)) / 2
    delay = (t4 - t1) - (t3 - t2)
    if abs(offset) > MAX_SANE_OFFSET or delay < 0:
        raise NtpError(f"{server}: implausible answer")
    return NtpSample(server, offset, delay, datetime.fromtimestamp(t4 + offset, timezone.utc))


def best_sample(servers: tuple[str, ...] | list[str], transport: Transport = udp_transport,
                wall: Callable[[], float] = time.time, timeout: float = 2.0, want: int = 2) -> tuple[NtpSample | None, list[str]]:
    """Query servers until ``want`` answers; return the lowest-delay sample and the errors seen."""
    samples: list[NtpSample] = []
    errors: list[str] = []
    for server in servers:
        try:
            samples.append(sntp_query(server, transport, wall, timeout))
        except NtpError as exc:
            errors.append(str(exc))
        if len(samples) >= want:
            break
    return (min(samples, key=lambda s: s.delay) if samples else None), errors
