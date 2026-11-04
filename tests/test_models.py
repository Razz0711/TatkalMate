from datetime import date

import pytest

from core.exceptions import ValidationError
from models.journey import Journey, TatkalCategory, TravelClass
from models.passenger import Gender, Passenger


def test_journey_normalizes_and_validates():
    j = Journey(" 12951 ", "st", " ndls", date(2026, 11, 20), TravelClass.AC_3_TIER, train_name="  Mumbai   Rajdhani ")
    v = j.validate()
    assert (v.train_number, v.from_station, v.to_station, v.train_name) == ("12951", "ST", "NDLS", "Mumbai Rajdhani")


@pytest.mark.parametrize("kwargs,field", [
    (dict(train_number="1295"), "train_number"),
    (dict(train_number="12a51"), "train_number"),
    (dict(from_station="S1"), "from_station"),
    (dict(to_station="ST"), "to_station"),
    (dict(travel_class=TravelClass.FIRST_AC), "travel_class"),
    (dict(boarding_day_offset=9), "boarding_day_offset"),
])
def test_journey_rejects_invalid(kwargs, field):
    base = dict(train_number="12951", from_station="ST", to_station="NDLS", journey_date=date(2026, 11, 20),
                travel_class=TravelClass.SLEEPER, category=TatkalCategory.TATKAL)
    base.update(kwargs)
    with pytest.raises(ValidationError) as e:
        Journey(**base).validate()
    assert field in e.value.errors


def test_passenger_validation():
    assert Passenger("  Raj   Kumar ", 22, Gender.MALE).validate().name == "Raj Kumar"
    for bad in [Passenger("Al", 22, Gender.MALE), Passenger("R4j", 22, Gender.MALE),
                Passenger("A" * 17, 22, Gender.MALE), Passenger("Raj", 3, Gender.MALE),
                Passenger("Raj", 126, Gender.MALE), Passenger("Raj", True, Gender.MALE)]:
        with pytest.raises(ValidationError):
            bad.validate()


def test_masked_name():
    assert Passenger("Raj Kumar", 22, Gender.MALE).masked_name == "R. K."
