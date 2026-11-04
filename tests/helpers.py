from datetime import datetime

from core.clock import FakeClock
from core.config import MonitoringConfig
from core.event_bus import EventBus
from core.scheduler import IST
from database.connection import Database
from database.models import migrate


def memory_db() -> Database:
    db = Database(":memory:")
    migrate(db)
    return db


def fake_clock(y=2026, m=11, d=19, hh=9, mm=0) -> FakeClock:
    return FakeClock(datetime(y, m, d, hh, mm, tzinfo=IST))


class Recorder:
    def __init__(self, bus: EventBus):
        self.events = []
        bus.subscribe(None, self.events.append)

    def of(self, event_type):
        return [e for e in self.events if e.type is event_type]


def fast_monitoring(**kw) -> MonitoringConfig:
    cfg = MonitoringConfig(poll_interval_seconds=0.05, stale_after_seconds=900, request_timeout_seconds=1,
                           backoff_base_seconds=0.05, backoff_max_seconds=0.2, failure_alert_threshold=2)
    for k, v in kw.items():
        setattr(cfg, k, v)
    return cfg
