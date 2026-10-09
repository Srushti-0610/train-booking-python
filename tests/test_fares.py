
import pytest

from app.fares import (
    BookingError,
    occupancy_band,
    fare,
    refund_rule,
    promote,
    seats_available,
    validate_seats,
)


# 1. Test fare bands

def test_occupancy_quiet_band():
    assert occupancy_band(40, 100) == ("quiet", 1.00)


def test_occupancy_filling_band():
    assert occupancy_band(41, 100) == ("filling", 1.15)


def test_occupancy_busy_band():
    assert occupancy_band(71, 100) == ("busy", 1.35)


def test_occupancy_last_seats_band():
    assert occupancy_band(91, 100) == ("last_seats", 1.60)


def test_zero_capacity_raises_error():
    with pytest.raises(BookingError):
        occupancy_band(0, 0)


# 2. Test fare calculation

def test_fare_returns_expected_details():
    result = fare(100, "SL", 20, 100)

    assert result["fare"] == 60
    assert result["band"] == "quiet"
    assert result["class"] == "SL"


def test_fare_increases_with_occupancy():
    quiet = fare(500, "SL", 20, 100)
    busy = fare(500, "SL", 80, 100)

    assert busy["fare"] > quiet["fare"]


# 3. Test refund rules

def test_refund_rule_at_48_hours():
    assert refund_rule(48)[0] == 1.00


def test_refund_rule_at_24_hours():
    assert refund_rule(24)[0] == 0.75


def test_refund_rule_at_12_hours():
    assert refund_rule(12)[0] == 0.50


def test_refund_rule_at_4_hours():
    assert refund_rule(4)[0] == 0.25


def test_refund_after_departure():
    assert refund_rule(-1)[0] == 0.0


# 4. Test available seats

def test_seats_available_counts_holds():
    assert seats_available(100, 70, 10) == 20


def test_seats_available_never_negative():
    assert seats_available(10, 12, 0) == 0


# 5. Test seat validation

def test_validate_seats():
    assert validate_seats(3) == 3


def test_validate_too_many_seats():
    with pytest.raises(BookingError):
        validate_seats(7)


# 6. Test waiting-list promotion

def test_promote_waitlisted_passenger():
    waitlist = [
        {"id": 1, "travel_class": "SL", "position": 1, "seats": 1},
        {"id": 2, "travel_class": "SL", "position": 2, "seats": 1},
        {"id": 3, "travel_class": "3A", "position": 1, "seats": 1},
    ]

    promoted, waiting = promote(waitlist, "SL", 1)

    assert [p["id"] for p in promoted] == [1]
    assert [w["id"] for w in waiting] == [2, 3]
    assert waiting[0]["position"] == 1


def test_party_is_not_split():
    waitlist = [
        {"id": 1, "travel_class": "SL", "position": 1, "seats": 3},
        {"id": 2, "travel_class": "SL", "position": 2, "seats": 2},
    ]

    promoted, waiting = promote(waitlist, "SL", 2)

    assert [p["id"] for p in promoted] == [2]
    assert [w["id"] for w in waiting] == [1]
    assert waiting[0]["position"] == 1