"""Application identity and version information."""

APP_NAME = "TatkalMate AI"
APP_ID = "TatkalMate"
__version__ = "0.2.0"
BUILD_CHANNEL = "alpha"


def version_string() -> str:
    return f"{APP_NAME} {__version__} ({BUILD_CHANNEL})"
