"""Duplicate process prevention.

A ``QLockFile`` (with stale-lock detection, so a crashed instance does not
block the next start) guarantees one instance per user. A ``QLocalServer``
lets a second launch ask the running instance to show its window.
"""

from __future__ import annotations

import getpass
import re
from pathlib import Path

from PySide6.QtCore import QLockFile, QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket


def _server_name() -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return "TatkalMate-" + re.sub(r"[^A-Za-z0-9_-]", "_", user)


class SingleInstance(QObject):
    activate_requested = Signal()

    def __init__(self, lock_path: Path):
        super().__init__()
        self._lock = QLockFile(str(lock_path))
        self._lock.setStaleLockTime(0)  # rely on PID liveness check, not age
        self._server: QLocalServer | None = None

    def acquire(self) -> bool:
        if not self._lock.tryLock(100):
            self._lock.removeStaleLockFile()
            if not self._lock.tryLock(100):
                return False
        name = _server_name()
        QLocalServer.removeServer(name)
        self._server = QLocalServer(self)
        self._server.newConnection.connect(self._on_connection)
        self._server.listen(name)
        return True

    def _on_connection(self) -> None:
        assert self._server is not None
        sock = self._server.nextPendingConnection()
        if sock:
            sock.waitForReadyRead(200)
            sock.readAll()
            sock.disconnectFromServer()
        self.activate_requested.emit()

    @staticmethod
    def signal_running_instance() -> bool:
        sock = QLocalSocket()
        sock.connectToServer(_server_name())
        if not sock.waitForConnected(500):
            return False
        sock.write(b"activate")
        sock.flush()
        sock.waitForBytesWritten(500)
        sock.disconnectFromServer()
        return True

    def release(self) -> None:
        if self._server:
            self._server.close()
        self._lock.unlock()
