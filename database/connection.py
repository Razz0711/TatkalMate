"""SQLite connection wrapper.

A single connection guarded by a re-entrant lock is shared between the GUI
thread and the asyncio thread. All statements are short, so contention is
negligible and this avoids SQLite "database is locked" errors from multiple
writers. WAL mode keeps reads non-blocking and makes the file crash-safe.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, Sequence

from core.exceptions import DatabaseError

log = logging.getLogger(__name__)


class Database:
    def __init__(self, path: Path | str):
        self.path = str(path)
        self._lock = threading.RLock()
        try:
            self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=10, isolation_level=None)
        except sqlite3.Error as exc:
            raise DatabaseError(f"cannot open database: {exc}") from exc
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            if self.path != ":memory:":
                self._conn.execute("PRAGMA journal_mode = WAL")
                self._conn.execute("PRAGMA synchronous = NORMAL")
        if self.path != ":memory:" and os.name != "nt":
            try:
                os.chmod(self.path, 0o600)
            except OSError:
                pass

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            try:
                return self._conn.execute(sql, params)
            except sqlite3.Error as exc:
                raise DatabaseError(str(exc)) from exc

    def query(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            try:
                return self._conn.execute(sql, params).fetchall()
            except sqlite3.Error as exc:
                raise DatabaseError(str(exc)) from exc

    def query_one(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Row | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                yield self._conn
                self._conn.execute("COMMIT")
            except sqlite3.Error as exc:
                self._conn.execute("ROLLBACK")
                raise DatabaseError(str(exc)) from exc
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise

    def ping(self) -> bool:
        try:
            return self.query_one("SELECT 1 AS ok")["ok"] == 1
        except DatabaseError:
            return False

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                log.exception("error closing database")
