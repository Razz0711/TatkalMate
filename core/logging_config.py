"""Structured logging with rotation and PII redaction.

* JSON lines are written to ``logs/tatkalmate.log`` (1 MB x 5 files).
* A redaction filter masks e-mail addresses, phone numbers, OTP/password
  fragments and any passenger name registered with :data:`REDACTOR`.
  Passenger names are registered by ``PassengerService`` at load time, so even
  an accidental ``log.info(passenger.name)`` is masked.
"""

from __future__ import annotations

import json
import logging
import re
import sys
import threading
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path

LOG_FILE_NAME = "tatkalmate.log"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 5

_PATTERNS = [
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[email]"),
    (re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)"), "[phone]"),
    (re.compile(r"(?i)\b(otp|password|passwd|pwd|pin|token|cookie|secret)\b\s*[:=]?\s*\S+"), r"\1=[redacted]"),
]


class Redactor:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._terms: set[str] = set()
        self._regex: re.Pattern[str] | None = None

    def register(self, *terms: str) -> None:
        with self._lock:
            for t in terms:
                t = (t or "").strip()
                if len(t) >= 2:
                    self._terms.add(t)
            self._rebuild()

    def clear(self) -> None:
        with self._lock:
            self._terms.clear()
            self._regex = None

    def _rebuild(self) -> None:
        if not self._terms:
            self._regex = None
            return
        alts = sorted((re.escape(t) for t in self._terms), key=len, reverse=True)
        self._regex = re.compile(r"(?i)\b(?:" + "|".join(alts) + r")\b")

    def redact(self, text: str) -> str:
        for pattern, repl in _PATTERNS:
            text = pattern.sub(repl, text)
        with self._lock:
            regex = self._regex
        if regex is not None:
            text = regex.sub("[passenger]", text)
        return text


REDACTOR = Redactor()


class RedactionFilter(logging.Filter):
    def __init__(self, redactor: Redactor = REDACTOR):
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            message = str(record.msg)
        record.msg = self._redactor.redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._redactor.redact(record.exc_text)
        return True


_STD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "thread": record.threadName,
            "msg": record.getMessage(),
        }
        extras = {k: v for k, v in vars(record).items() if k not in _STD_ATTRS}
        if extras:
            data["extra"] = {k: _safe(v) for k, v in extras.items()}
        if record.exc_text:
            data["exc"] = record.exc_text
        elif record.exc_info:
            data["exc"] = REDACTOR.redact(self.formatException(record.exc_info))
        return json.dumps(data, ensure_ascii=False)


def _safe(value: object) -> object:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return REDACTOR.redact(value) if isinstance(value, str) else value
    return REDACTOR.redact(repr(value))


def setup_logging(log_dir: Path, level: int = logging.INFO, console: bool = True) -> Path:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / LOG_FILE_NAME
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
        h.close()
    root.setLevel(level)

    redaction = RedactionFilter()
    file_handler = RotatingFileHandler(log_path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8")
    file_handler.setFormatter(JsonFormatter())
    file_handler.addFilter(redaction)
    root.addHandler(file_handler)

    if console and sys.stderr is not None:  # stderr is None in windowed PyInstaller builds
        stream = logging.StreamHandler()
        stream.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
        stream.addFilter(redaction)
        root.addHandler(stream)

    logging.captureWarnings(True)
    return log_path


def tail_log(log_path: Path, max_lines: int = 400) -> list[str]:
    if not log_path.exists():
        return []
    with log_path.open("r", encoding="utf-8", errors="replace") as fh:
        lines = fh.readlines()
    return [ln.rstrip("\n") for ln in lines[-max_lines:]]
