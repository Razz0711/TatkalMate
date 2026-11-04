"""Schema and migrations (tracked with ``PRAGMA user_version``)."""

from __future__ import annotations

import logging

from database.connection import Database

log = logging.getLogger(__name__)

MIGRATIONS: list[str] = [
    # v1
    """
    CREATE TABLE passengers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name_enc TEXT NOT NULL,
        age INTEGER NOT NULL CHECK (age BETWEEN 1 AND 125),
        gender TEXT NOT NULL CHECK (gender IN ('M','F','T')),
        berth_preference TEXT NOT NULL DEFAULT '',
        food_preference TEXT NOT NULL DEFAULT '',
        include_in_booking INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE journeys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        train_number TEXT NOT NULL,
        train_name TEXT NOT NULL DEFAULT '',
        from_station TEXT NOT NULL,
        to_station TEXT NOT NULL,
        journey_date TEXT NOT NULL,
        travel_class TEXT NOT NULL,
        category TEXT NOT NULL,
        boarding_day_offset INTEGER NOT NULL DEFAULT 0,
        is_active INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE availability_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        query_key TEXT NOT NULL,
        status TEXT NOT NULL,
        seats INTEGER,
        raw_text TEXT NOT NULL DEFAULT '',
        source TEXT NOT NULL,
        fetched_at TEXT NOT NULL,
        source_timestamp TEXT,
        is_cached INTEGER NOT NULL,
        confirmed_by_source INTEGER NOT NULL,
        simulated INTEGER NOT NULL,
        changed INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_avail_key_time ON availability_history (query_key, fetched_at);
    CREATE TABLE notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        kind TEXT NOT NULL,
        severity TEXT NOT NULL,
        title TEXT NOT NULL,
        body TEXT NOT NULL,
        dedupe_key TEXT NOT NULL,
        created_at TEXT NOT NULL,
        read INTEGER NOT NULL DEFAULT 0
    );
    CREATE INDEX idx_notif_dedupe ON notifications (dedupe_key, created_at);
    CREATE TABLE readiness_confirmations (
        item TEXT PRIMARY KEY,
        context_key TEXT NOT NULL,
        confirmed_at TEXT NOT NULL
    );
    CREATE TABLE app_state (
        key TEXT PRIMARY KEY,
        value TEXT NOT NULL
    );
    """,
]

SCHEMA_VERSION = len(MIGRATIONS)


def migrate(db: Database) -> int:
    current = db.query_one("PRAGMA user_version")[0]
    if current > SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema v{current} is newer than this app (v{SCHEMA_VERSION}); update TatkalMate"
        )
    for version in range(current, SCHEMA_VERSION):
        log.info("applying database migration", extra={"to_version": version + 1})
        with db.transaction() as conn:
            for statement in MIGRATIONS[version].split(";"):
                if statement.strip():
                    conn.execute(statement)
            conn.execute(f"PRAGMA user_version = {version + 1}")
    return SCHEMA_VERSION
