INSERT INTO stations (code, name, city) VALUES
 ('CSMT','Chhatrapati Shivaji Terminus','Mumbai'),
 ('NSK','Nashik Road','Nashik'),
 ('BPL','Bhopal Junction','Bhopal'),
 ('AGC','Agra Cantt','Agra'),
 ('NDLS','New Delhi','Delhi'),
 ('SBC','KSR Bengaluru','Bengaluru'),
 ('HYB','Hyderabad Deccan','Hyderabad'),
 ('PUNE','Pune Junction','Pune')
ON CONFLICT DO NOTHING;

-- Departures are relative to now(), so every refund band is reachable on a
-- freshly started stack: one train leaves in 3 hours (25% band), one in
-- 18 hours (50%), one in 30 hours (75%), one in 5 days (full refund).
INSERT INTO trains (id, number, name, departs_at, arrives_at) VALUES
 (1,'12951','Rajdhani Express',
    now() + interval '5 days', now() + interval '5 days 16 hours'),
 (2,'12137','Punjab Mail',
    now() + interval '30 hours', now() + interval '54 hours'),
 (3,'11301','Udyan Express',
    now() + interval '18 hours', now() + interval '38 hours'),
 (4,'12163','Dadar Chennai Express',
    now() + interval '3 hours', now() + interval '26 hours'),
 (5,'12627','Karnataka Express',
    now() + interval '5 days 4 hours', now() + interval '6 days 12 hours')
ON CONFLICT DO NOTHING;
SELECT setval('trains_id_seq', GREATEST((SELECT MAX(id) FROM trains),1));

-- Route: CSMT -> NSK -> BPL -> AGC -> NDLS, with running kilometres.
INSERT INTO train_stops (train_id, station_code, stop_order, km) VALUES
 (1,'CSMT',1,0),(1,'NSK',2,187),(1,'BPL',3,837),(1,'AGC',4,1264),(1,'NDLS',5,1447),
 (2,'CSMT',1,0),(2,'NSK',2,187),(2,'BPL',3,837),(2,'NDLS',4,1447),
 (3,'CSMT',1,0),(3,'PUNE',2,192),(3,'SBC',3,1210),
 (4,'CSMT',1,0),(4,'PUNE',2,192),(4,'HYB',3,711),
 (5,'SBC',1,0),(5,'HYB',2,610),(5,'BPL',3,1479),(5,'NDLS',4,2089)
ON CONFLICT DO NOTHING;

-- Train 1 has a DELIBERATELY TINY AC First class: two berths. Book two, then
-- book a third and watch it go to the waiting list. That is the demo.
INSERT INTO train_classes (train_id, travel_class, capacity) VALUES
 (1,'SL',72),(1,'3A',48),(1,'2A',24),(1,'1A',2),
 (2,'SL',72),(2,'3A',48),(2,'2A',18),
 (3,'SL',64),(3,'3A',32),
 (4,'SL',80),(4,'3A',40),(4,'2A',20),
 (5,'SL',72),(5,'3A',48),(5,'2A',24),(5,'1A',8)
ON CONFLICT DO NOTHING;

-- Existing bookings, so the fare bands are not all "quiet" on first run.
-- Train 1 sleeper is 58/72 sold, which lands it in the "busy" band.
INSERT INTO bookings (pnr, train_id, travel_class, passenger, seats, origin,
                      destination, distance_km, amount, status)
SELECT 'SEED' || lpad(g::text, 4, '0'), 1, 'SL',
       'Seed Passenger ' || g, 1, 'CSMT', 'NDLS', 1447, 820, 'CONFIRMED'
FROM generate_series(1, 58) AS g
ON CONFLICT DO NOTHING;

-- Train 1 AC 3 Tier is 20/48, i.e. the "quiet" band - the same journey in a
-- fuller class costs visibly more. Run GET /search and compare.
INSERT INTO bookings (pnr, train_id, travel_class, passenger, seats, origin,
                      destination, distance_km, amount, status)
SELECT 'SEED3A' || lpad(g::text, 2, '0'), 1, '3A',
       'Seed Passenger 3A ' || g, 1, 'CSMT', 'NDLS', 1447, 1736, 'CONFIRMED'
FROM generate_series(1, 20) AS g
ON CONFLICT DO NOTHING;

-- Train 3 sleeper is almost gone: 61/64, the "last_seats" band.
INSERT INTO bookings (pnr, train_id, travel_class, passenger, seats, origin,
                      destination, distance_km, amount, status)
SELECT 'SEEDU' || lpad(g::text, 3, '0'), 3, 'SL',
       'Seed Udyan ' || g, 1, 'CSMT', 'SBC', 1210, 871, 'CONFIRMED'
FROM generate_series(1, 61) AS g
ON CONFLICT DO NOTHING;
