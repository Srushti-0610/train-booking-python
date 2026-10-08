from datetime import date as date_cls
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import JSONResponse

from . import cache, db
from .fares import (
    CLASS_NAMES,
    CLASS_RATE,
    HOLD_SECONDS,
    BookingError,
    fare,
    next_position,
    promote,
    refund,
    seats_available,
    validate_seats,
)

app = FastAPI(title="train-booking")

SEARCH_TTL = 60          # searches are read far more often than trains change


@app.get("/health")
def health():
    out = {"status": "ok", "postgres": False, "redis": False}
    try:
        db.query("SELECT 1")
        out["postgres"] = True
    except Exception as e:
        out["pg_error"] = str(e)
    try:
        cache.client().ping()
        out["redis"] = True
    except Exception as e:
        out["redis_error"] = str(e)
    return out if out["postgres"] and out["redis"] else JSONResponse(out, status_code=503)


def _now():
    return datetime.now(timezone.utc)


def _held(train_id, travel_class):
    """Seats currently held by somebody mid-checkout, for this class.

    Each hold is its own Redis key with a TTL, so an abandoned checkout
    releases its seats without anybody running a sweeper.
    """
    total = 0
    for key in cache.client().scan_iter(f"hold:{train_id}:{travel_class}:*"):
        payload = cache.get_json(key)
        if payload:
            total += int(payload.get("seats", 0))
    return total


def _class_row(train_id, travel_class):
    row = db.one("SELECT tc.id, tc.travel_class, tc.capacity,"
                 " (SELECT coalesce(sum(b.seats),0) FROM bookings b"
                 "  WHERE b.train_id=tc.train_id"
                 "  AND b.travel_class=tc.travel_class AND b.status='CONFIRMED') AS confirmed"
                 " FROM train_classes tc WHERE tc.train_id=%s AND tc.travel_class=%s",
                 (train_id, str(travel_class).upper()))
    if not row:
        raise HTTPException(404, "that train does not run that class")
    return row


def _waitlist(train_id):
    return [{"id": r["id"], "travel_class": r["travel_class"],
             "position": r["wl_position"], "seats": r["seats"]}
            for r in db.query(
                "SELECT id, travel_class, wl_position, seats FROM bookings"
                " WHERE train_id=%s AND status='WAITLISTED'"
                " ORDER BY wl_position, id", (train_id,))]


def _distance(train_id, src, dst):
    """Kilometres from src to dst, and a hard no if the train does not run
    that way round. A train from Mumbai to Delhi does not take you back."""
    rows = db.query("SELECT station_code, km, stop_order FROM train_stops"
                    " WHERE train_id=%s ORDER BY stop_order", (train_id,))
    order = {r["station_code"]: (r["stop_order"], r["km"]) for r in rows}
    if src not in order or dst not in order:
        return None
    a, b = order[src], order[dst]
    if a[0] >= b[0]:
        return None
    return b[1] - a[1]


@app.get("/search")
def search(origin: str = "", destination: str = "", date: str = ""):
    """Trains between two stations on a date, with live availability and fare.

    Redis earns its place here. A search is read hundreds of times for every
    time the underlying data changes - the whole class refreshes the same
    Mumbai-Delhi page - so the assembled result is cached for a minute and
    dropped the moment a hold, a booking or a cancellation changes it.
    """
    src, dst = origin.strip().upper(), destination.strip().upper()
    if not src or not dst:
        raise HTTPException(400, "origin and destination are required")
    if src == dst:
        raise HTTPException(400, "origin and destination are the same station")
    if not date:
        raise HTTPException(400, "date is required, as YYYY-MM-DD")
    try:
        date_cls.fromisoformat(date)
    except ValueError:
        raise HTTPException(400, "date must be YYYY-MM-DD")

    key = f"search:{src}:{dst}:{date}"
    hit = cache.get_json(key)
    if hit:
        return {**hit, "cached": True}

    trains = db.query(
        "SELECT t.id, t.number, t.name, t.departs_at, t.arrives_at"
        " FROM trains t WHERE t.departs_at::date = %s::date ORDER BY t.departs_at",
        (date,))
    results = []
    for t in trains:
        km = _distance(t["id"], src, dst)
        if km is None:
            continue
        classes = []
        for c in db.query(
            "SELECT travel_class, capacity,"
            " (SELECT coalesce(sum(b.seats),0) FROM bookings b"
            "  WHERE b.train_id=tc.train_id"
            "  AND b.travel_class=tc.travel_class AND b.status='CONFIRMED') AS confirmed,"
            " (SELECT coalesce(sum(b.seats),0) FROM bookings b"
            "  WHERE b.train_id=tc.train_id"
            "  AND b.travel_class=tc.travel_class AND b.status='WAITLISTED') AS waitlisted_seats"
            " FROM train_classes tc WHERE tc.train_id=%s"
            " ORDER BY array_position(ARRAY['SL','3A','2A','1A'], tc.travel_class)",
                (t["id"],)):
            holds = _held(t["id"], c["travel_class"])
            free = seats_available(c["capacity"], c["confirmed"], holds)
            priced = fare(float(km), c["travel_class"], c["confirmed"], c["capacity"])
            classes.append({**priced, "capacity": c["capacity"],
                            "confirmed": int(c["confirmed"]),
                            "held": holds, "available": free,
                            "waitlisted_seats": int(c["waitlisted_seats"]),
                            "status": "AVAILABLE" if free else "WAITLIST"})
        results.append({"train_id": t["id"], "number": t["number"], "name": t["name"],
                        "departs_at": t["departs_at"].isoformat(),
                        "arrives_at": t["arrives_at"].isoformat(),
                        "distance_km": float(km), "classes": classes})
    if not results:
        raise HTTPException(404, "no train runs from there to there on that date")
    out = {"origin": src, "destination": dst, "date": date, "trains": results}
    cache.set_json(key, out, ttl=SEARCH_TTL)
    return {**out, "cached": False}


@app.post("/holds", status_code=201)
def hold(payload: dict = Body(...)):
    """Hold seats for a few minutes while the passenger pays."""
    train = db.one("SELECT id FROM trains WHERE id=%s", (payload.get("train_id"),))
    if not train:
        raise HTTPException(404, "no such train")
    cls = str(payload.get("travel_class", "")).upper()
    row = _class_row(train["id"], cls)
    try:
        seats = validate_seats(payload.get("seats", 1))
    except BookingError as e:
        raise HTTPException(400, str(e))

    free = seats_available(row["capacity"], row["confirmed"], _held(train["id"], cls))
    if free < seats:
        raise HTTPException(409, f"only {free} seats free in {CLASS_NAMES.get(cls, cls)};"
                                 f" book without a hold to join the waiting list")
    hid = str(uuid4())[:8]
    cache.set_json(f"hold:{train['id']}:{cls}:{hid}",
                   {"seats": seats, "train_id": train["id"], "travel_class": cls},
                   ttl=HOLD_SECONDS)
    cache.drop_prefix("search:")
    return {"hold_id": hid, "train_id": train["id"], "travel_class": cls,
            "seats": seats, "expires_in_seconds": HOLD_SECONDS}


@app.post("/bookings", status_code=201)
def book(payload: dict = Body(...)):
    """Book, or join the waiting list if the class is full."""
    train = db.one("SELECT id, departs_at FROM trains WHERE id=%s",
                   (payload.get("train_id"),))
    if not train:
        raise HTTPException(404, "no such train")
    passenger = str(payload.get("passenger", "")).strip()
    if not passenger:
        raise HTTPException(400, "passenger is required")
    cls = str(payload.get("travel_class", "")).upper()
    if cls not in CLASS_RATE:
        raise HTTPException(400, f"travel_class must be one of {sorted(CLASS_RATE)}")
    src = str(payload.get("origin", "")).upper()
    dst = str(payload.get("destination", "")).upper()
    km = _distance(train["id"], src, dst)
    if km is None:
        raise HTTPException(400, "this train does not run from there to there,"
                                 " in that order")
    try:
        seats = validate_seats(payload.get("seats", 1))
    except BookingError as e:
        raise HTTPException(400, str(e))
    if train["departs_at"] <= _now():
        raise HTTPException(409, "that train has already left")

    hold_id = payload.get("hold_id")
    hold_key = f"hold:{train['id']}:{cls}:{hold_id}" if hold_id else None
    held_by_me = 0
    if hold_key:
        payload_hold = cache.get_json(hold_key)
        if not payload_hold:
            raise HTTPException(409, "that hold has expired")
        held_by_me = int(payload_hold.get("seats", 0))

    with db.connect() as conn, conn.cursor() as cur:
        # Lock the class row so two passengers cannot both see the last seat.
        cur.execute("SELECT capacity FROM train_classes WHERE train_id=%s"
                    " AND travel_class=%s FOR UPDATE", (train["id"], cls))
        row = cur.fetchone()
        if not row:
            raise HTTPException(404, "that train does not run that class")
        # SEATS, not rows. A booking can be a party of six, and counting rows
        # here would quietly sell the same berths over and over.
        cur.execute("SELECT coalesce(sum(seats),0) AS n FROM bookings"
                    " WHERE train_id=%s AND travel_class=%s AND status='CONFIRMED'",
                    (train["id"], cls))
        confirmed = int(cur.fetchone()["n"])
        others_holding = max(0, _held(train["id"], cls) - held_by_me)
        free = seats_available(row["capacity"], confirmed, others_holding)

        priced = fare(float(km), cls, confirmed, row["capacity"])
        amount = priced["fare"] * seats
        pnr = str(uuid4())[:8].upper()

        if free >= seats:
            cur.execute(
                "INSERT INTO bookings (pnr, train_id, travel_class, passenger, seats,"
                " origin, destination, distance_km, amount, status)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'CONFIRMED') RETURNING id",
                (pnr, train["id"], cls, passenger, seats, src, dst, km, amount))
            status, position = "CONFIRMED", None
        else:
            cur.execute("SELECT id, travel_class, wl_position AS position, seats"
                        " FROM bookings WHERE train_id=%s AND status='WAITLISTED'",
                        (train["id"],))
            position = next_position([dict(r) for r in cur.fetchall()], cls)
            cur.execute(
                "INSERT INTO bookings (pnr, train_id, travel_class, passenger, seats,"
                " origin, destination, distance_km, amount, status, wl_position)"
                " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'WAITLISTED',%s) RETURNING id",
                (pnr, train["id"], cls, passenger, seats, src, dst, km, amount, position))
            status = "WAITLISTED"

    if hold_key:
        cache.drop(hold_key)
    cache.drop_prefix("search:")
    return {"pnr": pnr, "status": status, "waitlist_position": position,
            "train_id": train["id"], "travel_class": cls, "class_name": priced["class_name"],
            "passenger": passenger, "seats": seats, "distance_km": float(km),
            "fare_each": priced["fare"], "amount": amount,
            "fare_band": priced["band"], "multiplier": priced["multiplier"]}


@app.get("/bookings/{pnr}")
def booking(pnr: str):
    row = db.one("SELECT b.pnr, b.passenger, b.travel_class, b.seats, b.origin,"
                 " b.destination, b.distance_km, b.amount, b.status, b.wl_position,"
                 " b.refunded, t.number, t.name, t.departs_at"
                 " FROM bookings b JOIN trains t ON t.id=b.train_id WHERE b.pnr=%s",
                 (pnr.upper(),))
    if not row:
        raise HTTPException(404, "no booking with that PNR")
    out = {**row, "distance_km": float(row["distance_km"]),
           "amount": float(row["amount"]),
           "refunded": float(row["refunded"]) if row["refunded"] is not None else None,
           "class_name": CLASS_NAMES.get(row["travel_class"], ""),
           "departs_at": row["departs_at"].isoformat()}
    if row["status"] != "CANCELLED":
        out["if_cancelled_now"] = refund(float(row["amount"]), row["departs_at"],
                                         _now(), row["status"])
    return out


@app.delete("/bookings/{pnr}")
def cancel(pnr: str):
    """Cancel, refund by the published scale, and promote the waiting list.

    THE HARD PART. The seat does not simply disappear - the first waitlisted
    passenger IN THAT CLASS must be confirmed automatically, in the same
    transaction, or two people end up holding one berth. The cancellation and
    the promotion are one atomic act, and the class row is locked for the whole
    of it.
    """
    row = db.one("SELECT id, train_id, travel_class, status, amount, seats"
                 " FROM bookings WHERE pnr=%s", (pnr.upper(),))
    if not row:
        raise HTTPException(404, "no booking with that PNR")
    if row["status"] == "CANCELLED":
        raise HTTPException(409, "that booking is already cancelled")
    train = db.one("SELECT departs_at FROM trains WHERE id=%s", (row["train_id"],))

    money = refund(float(row["amount"]), train["departs_at"], _now(), row["status"])
    promoted = []
    with db.connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT capacity FROM train_classes WHERE train_id=%s"
                    " AND travel_class=%s FOR UPDATE",
                    (row["train_id"], row["travel_class"]))
        cur.execute("UPDATE bookings SET status='CANCELLED', wl_position=NULL,"
                    " refunded=%s, cancelled_at=now() WHERE id=%s AND status<>'CANCELLED'",
                    (money["refund"], row["id"]))
        if cur.rowcount == 0:
            raise HTTPException(409, "that booking is already cancelled")

        if row["status"] == "CONFIRMED":
            cur.execute("SELECT id, travel_class, wl_position AS position, seats,"
                        " pnr, passenger FROM bookings WHERE train_id=%s"
                        " AND status='WAITLISTED'", (row["train_id"],))
            waiting = [dict(r) for r in cur.fetchall()]
            moved_up, still = promote(waiting, row["travel_class"], row["seats"])
            for w in moved_up:
                cur.execute("UPDATE bookings SET status='CONFIRMED', wl_position=NULL"
                            " WHERE id=%s", (w["id"],))
                promoted.append({"pnr": w["pnr"], "passenger": w["passenger"],
                                 "seats": w["seats"],
                                 "was_position": w["position"],
                                 "now": "CONFIRMED"})
            for w in still:
                if str(w["travel_class"]).upper() == str(row["travel_class"]).upper():
                    cur.execute("UPDATE bookings SET wl_position=%s WHERE id=%s",
                                (w["position"], w["id"]))
        else:
            # A waitlisted passenger left the queue: close the gap behind them.
            cur.execute("SELECT id, travel_class, wl_position AS position, seats"
                        " FROM bookings WHERE train_id=%s AND status='WAITLISTED'",
                        (row["train_id"],))
            _, still = promote([dict(r) for r in cur.fetchall()],
                               row["travel_class"], 0)
            for w in still:
                if str(w["travel_class"]).upper() == str(row["travel_class"]).upper():
                    cur.execute("UPDATE bookings SET wl_position=%s WHERE id=%s",
                                (w["position"], w["id"]))

    cache.drop_prefix("search:")
    return {"pnr": pnr.upper(), "cancelled": True, **money,
            "seats_released": row["seats"], "promoted": promoted}


@app.get("/trains/{train_id}/waitlist")
def waitlist(train_id: int):
    if not db.one("SELECT id FROM trains WHERE id=%s", (train_id,)):
        raise HTTPException(404, "no such train")
    rows = db.query("SELECT pnr, passenger, travel_class, seats, wl_position"
                    " FROM bookings WHERE train_id=%s AND status='WAITLISTED'"
                    " ORDER BY travel_class, wl_position", (train_id,))
    by_class = {}
    for r in rows:
        by_class.setdefault(r["travel_class"], []).append(
            {k: v for k, v in r.items() if k != "travel_class"})
    return {"train_id": train_id, "waitlist_by_class": by_class,
            "note": "positions are per class - cancelling a sleeper berth never"
                    " promotes somebody waiting for AC"}
