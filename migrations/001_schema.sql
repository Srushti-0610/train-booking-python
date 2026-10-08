CREATE TABLE IF NOT EXISTS stations (
    code TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    city TEXT NOT NULL DEFAULT '');

CREATE TABLE IF NOT EXISTS trains (
    id SERIAL PRIMARY KEY,
    number TEXT UNIQUE NOT NULL,
    name TEXT NOT NULL,
    departs_at TIMESTAMPTZ NOT NULL,
    arrives_at TIMESTAMPTZ NOT NULL,
    CHECK (arrives_at > departs_at));

CREATE TABLE IF NOT EXISTS train_stops (
    train_id INT NOT NULL REFERENCES trains(id) ON DELETE CASCADE,
    station_code TEXT NOT NULL REFERENCES stations(code),
    stop_order INT NOT NULL,
    km NUMERIC(8,1) NOT NULL CHECK (km >= 0),
    PRIMARY KEY (train_id, station_code));
CREATE INDEX IF NOT EXISTS train_stops_order ON train_stops (train_id, stop_order);

CREATE TABLE IF NOT EXISTS train_classes (
    id SERIAL PRIMARY KEY,
    train_id INT NOT NULL REFERENCES trains(id) ON DELETE CASCADE,
    travel_class TEXT NOT NULL,
    capacity INT NOT NULL CHECK (capacity > 0),
    UNIQUE (train_id, travel_class));

CREATE TABLE IF NOT EXISTS bookings (
    id SERIAL PRIMARY KEY,
    pnr TEXT UNIQUE NOT NULL,
    train_id INT NOT NULL REFERENCES trains(id) ON DELETE CASCADE,
    travel_class TEXT NOT NULL,
    passenger TEXT NOT NULL,
    seats INT NOT NULL CHECK (seats > 0),
    origin TEXT NOT NULL,
    destination TEXT NOT NULL,
    distance_km NUMERIC(8,1) NOT NULL,
    amount NUMERIC(10,2) NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('CONFIRMED','WAITLISTED','CANCELLED')),
    wl_position INT,
    refunded NUMERIC(10,2),
    booked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    cancelled_at TIMESTAMPTZ);
CREATE INDEX IF NOT EXISTS bookings_live
    ON bookings (train_id, travel_class, status);
CREATE INDEX IF NOT EXISTS bookings_waitlist
    ON bookings (train_id, status, wl_position);
