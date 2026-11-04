"""TatkalMate AI entry point."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.config import load_dotenv  # noqa: E402
from core.logging_config import setup_logging  # noqa: E402
from core.paths import resolve_paths  # noqa: E402
from core.recovery import SessionGuard, install_crash_handlers  # noqa: E402
from core.version import APP_ID, APP_NAME, version_string  # noqa: E402

log = logging.getLogger("tatkalmate")


def parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="TatkalMate", description=APP_NAME)
    p.add_argument("--version", action="store_true", help="print version and exit")
    p.add_argument("--data-dir", type=Path, help="override the data folder")
    p.add_argument("--debug", action="store_true", help="verbose logging")
    p.add_argument("--smoke-test", type=float, metavar="SECONDS",
                   help="start, run for SECONDS, then quit (used by CI)")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    if args.version:
        print(version_string())
        return 0

    load_dotenv(ROOT / ".env")
    paths = resolve_paths(args.data_dir)
    setup_logging(paths.log_dir, logging.DEBUG if args.debug else logging.INFO)
    log.info("starting", extra={"version": version_string(), "platform": sys.platform})

    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QMessageBox

    from app.bridge import UiBridge
    from app.single_instance import SingleInstance
    from app.styles import stylesheet

    if sys.platform.startswith("win"):
        try:  # group the taskbar icon and toasts under our own app id
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"{APP_ID}.Desktop")
        except Exception:
            pass

    app = QApplication(sys.argv[:1])
    app.setApplicationName(APP_NAME)
    app.setOrganizationName(APP_ID)
    app.setQuitOnLastWindowClosed(True)
    app.setStyle("Fusion")
    app.setStyleSheet(stylesheet())

    instance = SingleInstance(paths.lock_file)
    if not instance.acquire():
        log.info("another instance is running; activating it")
        if not SingleInstance.signal_running_instance():
            QMessageBox.information(None, APP_NAME, "TatkalMate is already running.")
        return 0

    crash_dialogs: list[QMessageBox] = []

    def on_crash(summary: str, report: Path | None) -> None:
        # Non-modal so a crash inside a timer or background callback cannot freeze the app.
        def show() -> None:
            box = QMessageBox(QMessageBox.Icon.Critical, APP_NAME,
                              f"An unexpected error occurred:\n\n{summary}\n\n"
                              f"A report was saved to:\n{report or 'the crashes folder'}\n\n"
                              "TatkalMate will keep running; restart it if something looks wrong.")
            box.setModal(False)
            box.finished.connect(lambda _r, b=box: crash_dialogs.remove(b) if b in crash_dialogs else None)
            crash_dialogs.append(box)
            box.show()

        QTimer.singleShot(0, show)

    install_crash_handlers(paths.crash_dir, on_crash)
    guard = SessionGuard(paths.session_file)
    previous_unclean = guard.begin()

    from app.main_window import MainWindow
    from models.notification import NotificationKind, Severity
    from services.container import build_context

    try:
        ctx = build_context(paths)
    except Exception as exc:
        log.exception("startup failed")
        QMessageBox.critical(None, APP_NAME, f"TatkalMate could not start:\n\n{exc}\n\nSee the log in {paths.log_dir}")
        instance.release()
        return 1

    bridge = UiBridge(ctx.bus)
    window = MainWindow(ctx, bridge)
    instance.activate_requested.connect(window.show_and_raise)
    ctx.start_background()
    window.show()

    if previous_unclean:
        ctx.notifications.notify(NotificationKind.SYSTEM, "Recovered from an unexpected exit",
                                 "TatkalMate did not shut down cleanly last time. Your journey, passengers and "
                                 "settings were restored. Restart monitoring if you need it.",
                                 Severity.WARNING, dedupe_key="recovered")

    def shutdown() -> None:
        log.info("graceful shutdown")
        bridge.close()
        ctx.shutdown()
        guard.end()
        instance.release()

    app.aboutToQuit.connect(shutdown)
    if args.smoke_test:
        QTimer.singleShot(int(args.smoke_test * 1000), app.quit)
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
