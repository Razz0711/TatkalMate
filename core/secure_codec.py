"""At-rest protection for personal fields (passenger names).

On Windows, values are encrypted with DPAPI (``CryptProtectData``) bound to the
current Windows user, so copying the database to another account or machine
does not reveal names. On other platforms (development/CI) values are stored
with a ``plain:`` prefix and the database file is restricted to the owner
(mode 0600). The health check reports which mode is active.
"""

from __future__ import annotations

import base64
import logging
import sys

log = logging.getLogger(__name__)

_ENTROPY = b"TatkalMate.passenger.v1"


class SecureCodec:
    def __init__(self, use_dpapi: bool | None = None):
        if use_dpapi is None:
            use_dpapi = sys.platform.startswith("win")
        self._dpapi = _Dpapi() if use_dpapi else None
        if self._dpapi and not self._dpapi.available:
            self._dpapi = None

    @property
    def mode(self) -> str:
        return "dpapi" if self._dpapi else "plain"

    def encode(self, text: str) -> str:
        if self._dpapi:
            try:
                return "dpapi:" + base64.b64encode(self._dpapi.protect(text.encode("utf-8"))).decode("ascii")
            except OSError:
                log.exception("DPAPI encryption failed; storing value unencrypted")
        return "plain:" + text

    def decode(self, stored: str) -> str:
        if stored.startswith("plain:"):
            return stored[6:]
        if stored.startswith("dpapi:"):
            if not self._dpapi:
                raise ValueError("value was encrypted with Windows DPAPI on another system")
            return self._dpapi.unprotect(base64.b64decode(stored[6:])).decode("utf-8")
        return stored  # legacy/unprefixed


class _Dpapi:  # pragma: no cover - Windows only
    def __init__(self) -> None:
        self.available = False
        try:
            import ctypes
            from ctypes import wintypes

            class Blob(ctypes.Structure):
                _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

            self._ctypes = ctypes
            self._Blob = Blob
            self._crypt32 = ctypes.windll.crypt32
            self._kernel32 = ctypes.windll.kernel32
            self.available = True
        except Exception:
            self.available = False

    def _blob(self, data: bytes):
        buf = self._ctypes.create_string_buffer(data, len(data))
        return self._Blob(len(data), self._ctypes.cast(buf, self._ctypes.POINTER(self._ctypes.c_char))), buf

    def _call(self, fn, data: bytes) -> bytes:
        ctypes = self._ctypes
        blob_in, _buf = self._blob(data)
        entropy, _ebuf = self._blob(_ENTROPY)
        blob_out = self._Blob()
        CRYPTPROTECT_UI_FORBIDDEN = 0x01
        if not fn(ctypes.byref(blob_in), None, ctypes.byref(entropy), None, None,
                  CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(blob_out)):
            raise OSError("DPAPI call failed")
        try:
            return ctypes.string_at(blob_out.pbData, blob_out.cbData)
        finally:
            self._kernel32.LocalFree(blob_out.pbData)

    def protect(self, data: bytes) -> bytes:
        crypt32 = self._crypt32
        ctypes = self._ctypes

        def fn(i, _desc, e, r, p, flags, o):
            return crypt32.CryptProtectData(i, ctypes.c_wchar_p("TatkalMate"), e, r, p, flags, o)

        return self._call(fn, data)

    def unprotect(self, data: bytes) -> bytes:
        crypt32 = self._crypt32

        def fn(i, desc, e, r, p, flags, o):
            return crypt32.CryptUnprotectData(i, desc, e, r, p, flags, o)

        return self._call(fn, data)
