"""Pure fare, refund and waiting-list rules. No database, no HTTP.

Three separate problems live here and none of them needs a database:

1. **What does a seat cost?** A base rate per kilometre by class, multiplied by
   an occupancy band. An empty train is cheap, a nearly full one is not.
2. **What do you get back if you cancel?** A sliding scale by hours before
   departure, with a flat clerkage fee that is never refunded.
3. **Who gets the seat that was just freed?** The waiting list, in the order
   people joined it, per class - never globally.

Every function takes the clock as an argument. Nothing here calls
``datetime.now()``, so a test can sit the train two hours from departure
without touching the system clock.

What to test
------------
* ``occupancy_band`` - the band edges, which are 40%, 70% and 90% and are
  INCLUSIVE at the top: a train exactly 40% sold is still "quiet", and 40.1%
  is not. Test both sides of all three, plus 0% and a completely full class.
* ``fare`` - zero distance, a one-stop hop, the full run; each class; each
  band. The same journey must cost more on a fuller train and the multiplier
  must apply to the distance-based fare, not be added to it.
* ``fare`` rounding - fares come out as whole rupees. Check that 0.5 does not
  silently drift between Python versions (this module rounds half UP, which is
  what a railway does and is NOT what Python's ``round`` does).
* ``refund_rule`` / ``refund`` - every band boundary: 48h, 24h, 12h, 4h, after
  departure, and a cancellation made AFTER the train left (zero, not negative).
  The clerkage fee must never make a refund negative.
* ``refund`` on a waitlisted ticket - never confirmed, so it is fully
  refundable minus nothing. That is a real railway rule and an easy one to miss.
* ``promotion_order`` - the heart of it. Waitlist position is per class.
  Cancelling a sleeper seat must not promote somebody waiting for AC. Ordering
  is by the position they were given, never by name, price or luck.
* ``promote`` - freeing N seats promotes exactly the first N, renumbers the
  rest from 1, and does nothing at all when the list is empty.
* ``seats_available`` - confirmed seats AND live holds both reduce it. A hold
  that has expired does not. Note the word SEATS: a booking can be a party of
  six, so anything that counts booking ROWS to work out availability will
  cheerfully sell the same berth six times. That is the single easiest bug to
  write in this whole project.
"""
from datetime import timedelta

# Rupees per kilometre, before any occupancy multiplier.
CLASS_RATE = {
    "SL": 0.45,      # sleeper
    "3A": 1.20,      # three-tier air conditioned
    "2A": 1.75,      # two-tier air conditioned
    "1A": 2.90,      # first class air conditioned
}
CLASS_NAMES = {"SL": "Sleeper", "3A": "AC 3 Tier", "2A": "AC 2 Tier", "1A": "AC First"}

# Minimum fare - a two-station hop still costs something.
MIN_FARE = {"SL": 60, "3A": 180, "2A": 250, "1A": 420}

# (upper bound on fraction sold, band name, multiplier). Read top to bottom;
# the first band whose bound the occupancy does NOT exceed wins.
FARE_BANDS = (
    (0.40, "quiet", 1.00),
    (0.70, "filling", 1.15),
    (0.90, "busy", 1.35),
    (1.01, "last_seats", 1.60),
)

# (minimum hours before departure, fraction refunded, label)
REFUND_BANDS = (
    (48, 1.00, "more than 48 hours"),
    (24, 0.75, "24 to 48 hours"),
    (12, 0.50, "12 to 24 hours"),
    (4,  0.25, "4 to 12 hours"),
)
CLERKAGE = 30.0          # flat fee, never refunded on a confirmed ticket

MAX_SEATS_PER_BOOKING = 6
HOLD_SECONDS = 300       # how long a seat is held while you pay


class BookingError(ValueError):
    pass


def _round_half_up(value):
    """Round half UP, the way a ticket counter does.

    Python's built-in round() uses banker's rounding: round(0.5) is 0 and
    round(1.5) is 2. A railway would never explain that to a passenger.
    """
    return int(value + 0.5) if value >= 0 else -int(-value + 0.5)


# --------------------------------------------------------------------------
# Fares
# --------------------------------------------------------------------------

def occupancy_band(sold, capacity):
    """Which pricing band is this class in?

    Returns (band_name, multiplier). The bounds are INCLUSIVE at the top: a
    train exactly 40% sold is still "quiet". Pick a side, write it down, test
    it - half the bugs in pricing code live on these edges.
    """
    if capacity <= 0:
        raise BookingError("a class with no seats has no occupancy")
    sold = max(0, int(sold))
    frac = sold / float(capacity)
    for bound, name, mult in FARE_BANDS:
        if frac <= bound:
            return name, mult
    return FARE_BANDS[-1][1], FARE_BANDS[-1][2]


def base_fare(distance_km, travel_class):
    """The distance part of the fare, before occupancy."""
    cls = str(travel_class).upper()
    if cls not in CLASS_RATE:
        raise BookingError(f"unknown class {travel_class!r}")
    if distance_km < 0:
        raise BookingError("distance cannot be negative")
    return max(float(distance_km) * CLASS_RATE[cls], float(MIN_FARE[cls]))


def fare(distance_km, travel_class, sold, capacity):
    """What one seat costs right now.

    The multiplier scales the distance fare - it is not added to it, which
    matters a lot on a long journey.
    """
    band, mult = occupancy_band(sold, capacity)
    amount = _round_half_up(base_fare(distance_km, travel_class) * mult)
    return {"fare": amount, "band": band, "multiplier": mult,
            "base": _round_half_up(base_fare(distance_km, travel_class)),
            "class": str(travel_class).upper(),
            "class_name": CLASS_NAMES.get(str(travel_class).upper(), "")}


def seats_available(capacity, confirmed, live_holds=0):
    """Seats a new passenger could actually take.

    A seat somebody is part-way through paying for is not available, even
    though nobody is confirmed in it yet. Expired holds are the caller's job
    to exclude - this function trusts the number it is given.
    """
    return max(0, int(capacity) - int(confirmed) - int(live_holds))


# --------------------------------------------------------------------------
# Refunds
# --------------------------------------------------------------------------

def hours_before(departure, now):
    """Hours between now and departure. Negative once the train has left."""
    return (departure - now).total_seconds() / 3600.0


def refund_rule(hours):
    """(fraction refunded, label) for a cancellation this long before departure."""
    for min_hours, fraction, label in REFUND_BANDS:
        if hours >= min_hours:
            return fraction, label
    if hours >= 0:
        return 0.0, "less than 4 hours"
    return 0.0, "after departure"


def refund(amount_paid, departure, now, status="CONFIRMED"):
    """What the passenger gets back.

    A waitlisted ticket was never a seat, so it refunds in full with no
    clerkage. A confirmed ticket loses the flat fee on top of the sliding
    scale, and the result is clamped at zero - a cancellation never costs more
    than the ticket did.
    """
    paid = float(amount_paid)
    if paid < 0:
        raise BookingError("a ticket cannot have cost less than nothing")
    hours = hours_before(departure, now)
    if str(status).upper() in ("WAITLISTED", "WL"):
        return {"refund": _round_half_up(paid), "fraction": 1.0,
                "clerkage": 0.0, "band": "waitlisted - never confirmed",
                "hours_before_departure": round(hours, 2)}
    fraction, label = refund_rule(hours)
    gross = paid * fraction
    net = max(0.0, gross - CLERKAGE if gross > 0 else 0.0)
    return {"refund": _round_half_up(net), "fraction": fraction,
            "clerkage": CLERKAGE if gross > 0 else 0.0, "band": label,
            "hours_before_departure": round(hours, 2)}


# --------------------------------------------------------------------------
# The waiting list
# --------------------------------------------------------------------------

def promotion_order(waitlist, travel_class):
    """Who is next, for THIS class only.

    `waitlist` is [{"id":.., "travel_class":"SL", "position":1, "seats":1}, ...]

    THE HARD PART STARTS HERE. A waiting list is per class, not per train.
    Cancelling a sleeper berth must not confirm the person waiting for AC 2
    Tier - they are waiting for a different thing, and they will notice. The
    ordering is by the position they were given when they joined, and by
    nothing else: not by fare paid, not by when the row was updated, and
    certainly not by whatever order the database returned.
    """
    cls = str(travel_class).upper()
    same = [w for w in waitlist if str(w.get("travel_class", "")).upper() == cls]
    return sorted(same, key=lambda w: (int(w["position"]), int(w["id"])))


def promote(waitlist, travel_class, freed_seats):
    """Decide who moves up when `freed_seats` become available.

    Returns (promoted, still_waiting). `still_waiting` is renumbered from 1,
    because a waiting list with a hole at position 3 is a waiting list nobody
    can read.

    A party of three waiting on two freed seats is NOT split - you cannot
    confirm two of somebody's children and leave the third on the platform.
    They are skipped and the next party that fits is considered, which is how
    real reservation systems behave and is the single most-forgotten case.
    """
    if freed_seats < 0:
        raise BookingError("cannot free a negative number of seats")
    queue = promotion_order(waitlist, travel_class)
    left = int(freed_seats)
    promoted, remaining = [], []
    for entry in queue:
        want = int(entry.get("seats", 1))
        if want <= left:
            left -= want
            promoted.append({**entry, "status": "CONFIRMED"})
        else:
            remaining.append(entry)
    others = [w for w in waitlist
              if str(w.get("travel_class", "")).upper() != str(travel_class).upper()]
    renumbered = [{**e, "position": i + 1} for i, e in enumerate(remaining)]
    return promoted, renumbered + others


def next_position(waitlist, travel_class):
    """The position the next person to join this class's queue gets."""
    return len(promotion_order(waitlist, travel_class)) + 1


def hold_expiry(now, seconds=HOLD_SECONDS):
    """When a seat hold lapses. Short, because an abandoned checkout should
    not keep a seat off the market for the rest of the day."""
    return now + timedelta(seconds=seconds)


def validate_seats(seats):
    try:
        n = int(seats)
    except (TypeError, ValueError):
        raise BookingError("seats must be a whole number")
    if n < 1:
        raise BookingError("book at least one seat")
    if n > MAX_SEATS_PER_BOOKING:
        raise BookingError(f"at most {MAX_SEATS_PER_BOOKING} seats in one booking")
    return n
