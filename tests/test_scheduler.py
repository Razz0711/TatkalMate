from datetime import date, datetime, timedelta, timezone

import pytest

from core.clock import FakeClock
from core.scheduler import IST, Phase, TatkalScheduler, TatkalWindow, compute_schedule
from models.journey import Journey, TatkalCategory, TravelClass


def ist(y, m, d, hh=0, mm=0, ss=0, us=0):
    return datetime(y, m, d, hh, mm, ss, us, tzinfo=IST)


@pytest.mark.parametrize("cls", [TravelClass.AC_2_TIER, TravelClass.AC_3_TIER, TravelClass.AC_3_ECONOMY,
                                 TravelClass.AC_CHAIR_CAR, TravelClass.EXECUTIVE_CHAIR_CAR])
def test_ac_classes_open_at_10_previous_day(cls):
    s = compute_schedule(cls, date(2026, 11, 20))
    assert s.window is TatkalWindow.AC
    assert s.booking_date == date(2026, 11, 19)
    assert s.opens_at == ist(2026, 11, 19, 10, 0)


@pytest.mark.parametrize("cls", [TravelClass.SLEEPER, TravelClass.SECOND_SITTING, TravelClass.FIRST_CLASS])
def test_non_ac_classes_open_at_11(cls):
    s = compute_schedule(cls, date(2026, 11, 20))
    assert s.window is TatkalWindow.NON_AC
    assert s.opens_at == ist(2026, 11, 19, 11, 0)


def test_opening_is_based_on_origin_departure_not_boarding_date():
    # Train leaves origin on the 19th, reaches the boarding station after midnight on the 20th.
    s = compute_schedule(TravelClass.SLEEPER, date(2026, 11, 20), boarding_day_offset=1)
    assert s.origin_departure_date == date(2026, 11, 19)
    assert s.booking_date == date(2026, 11, 18)
    assert s.opens_at == ist(2026, 11, 18, 11, 0)


@pytest.mark.parametrize("journey,booking", [
    (date(2026, 1, 1), date(2025, 12, 31)),   # year boundary
    (date(2026, 3, 1), date(2026, 2, 28)),    # non-leap February
    (date(2028, 3, 1), date(2028, 2, 29)),    # leap year
    (date(2026, 12, 1), date(2026, 11, 30)),  # month boundary
])
def test_date_boundaries(journey, booking):
    assert compute_schedule(TravelClass.AC_3_TIER, journey).booking_date == booking


def test_opens_at_utc_is_0430_for_ac():
    s = compute_schedule(TravelClass.AC_3_TIER, date(2026, 11, 20))
    assert s.opens_at_utc == datetime(2026, 11, 19, 4, 30, tzinfo=timezone.utc)


def test_negative_offset_rejected():
    with pytest.raises(ValueError):
        compute_schedule(TravelClass.SLEEPER, date(2026, 11, 20), -1)


def _journey(**kw):
    base = dict(train_number="12951", from_station="ST", to_station="NDLS", journey_date=date(2026, 11, 20),
                travel_class=TravelClass.AC_3_TIER, category=TatkalCategory.TATKAL)
    base.update(kw)
    return Journey(**base)


def test_countdown_has_millisecond_precision_and_formats():
    clock = FakeClock(ist(2026, 11, 19, 8, 58, 30, 250_000))
    sch = TatkalScheduler(clock)
    sch.set_journey(_journey())
    cd = sch.countdown()
    assert cd.parts == (1, 1, 29, 750)
    assert cd.format() == "01:01:29.750"
    assert not cd.is_open


def test_countdown_hours_exceed_24():
    clock = FakeClock(ist(2026, 11, 17, 10, 0))
    sch = TatkalScheduler(clock)
    sch.set_journey(_journey())
    assert sch.countdown().format() == "48:00:00.000"


def test_countdown_clamps_at_zero_once_open():
    clock = FakeClock(ist(2026, 11, 19, 10, 0, 0, 1))
    sch = TatkalScheduler(clock)
    sch.set_journey(_journey())
    cd = sch.countdown()
    assert cd.is_open and cd.total_milliseconds == 0


def test_phases():
    clock = FakeClock(ist(2026, 11, 18, 12, 0))
    sch = TatkalScheduler(clock)
    assert sch.phase() is Phase.NO_JOURNEY
    sch.set_journey(_journey())
    assert sch.phase() is Phase.BEFORE_BOOKING_DAY
    clock.set_wall(ist(2026, 11, 19, 9, 59, 59))
    assert sch.phase() is Phase.BOOKING_DAY_PRE_OPEN
    clock.set_wall(ist(2026, 11, 19, 10, 0))
    assert sch.phase() is Phase.OPEN
    clock.set_wall(ist(2026, 11, 21, 0, 0))
    assert sch.phase() is Phase.JOURNEY_PASSED


def test_restart_recomputes_same_target():
    clock = FakeClock(ist(2026, 11, 19, 9, 0))
    a, b = TatkalScheduler(clock), TatkalScheduler(clock)
    a.set_journey(_journey())
    clock.advance(1234.5)
    b.set_journey(_journey())  # "restarted" app
    assert a.countdown() == b.countdown()


def test_clock_jump_detected_and_countdown_follows_wall_clock():
    clock = FakeClock(ist(2026, 11, 19, 9, 0))
    sch = TatkalScheduler(clock)
    sch.set_journey(_journey())
    assert sch.check_clock() is None
    clock.advance(1.0)
    assert sch.check_clock() is None
    clock.jump_wall(-600)  # user sets the clock back 10 minutes
    jump = sch.check_clock()
    assert jump is not None and jump.delta_seconds == pytest.approx(-600)
    assert sch.countdown().remaining == timedelta(hours=1, minutes=10, seconds=-1)
