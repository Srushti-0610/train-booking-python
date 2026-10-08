# Train Seat Booking with Fare Tiers

**Language:** Python (FastAPI) &nbsp;|&nbsp; **Needs:** Postgres + Redis

This is a **starter**. The application already works. Your job is everything
that gets it building, tested and running in CI.

---

## You do not need Python installed

You will build this into a container, and the container brings its own
Python 3.12. You are not being asked to extend the app — you are being asked
to ship it.

---

## 1. What this app needs

| | |
|---|---|
| **Runtime** | Python 3.12 |
| **Install dependencies** | `pip install -r requirements.txt` |
| **Start the app** | `uvicorn app.main:app --host 0.0.0.0 --port 8080` |
| **Listens on** | port 8080, bound to `0.0.0.0` |
| **Environment variables** | `DATABASE_URL`, `REDIS_URL` |
| **Needs running first** | Postgres, Redis, and the migrations applied |

### What it does

Search trains between two stations on a date, see availability and the live fare for every class, hold seats while you pay, and book. Fares rise in bands as the train fills. Cancelling refunds on a sliding scale that depends on how close to departure you are - and automatically confirms the first person on that class's waiting list.

### Endpoints

```
GET    /health
GET    /search?origin=CSMT&destination=NDLS&date=YYYY-MM-DD
POST   /holds      {"train_id":1,"travel_class":"1A","seats":1}
POST   /bookings   {"train_id":1,"travel_class":"1A","passenger":"Asha",
                    "origin":"CSMT","destination":"NDLS","seats":1,"hold_id":"..."}
GET    /bookings/{pnr}            includes what a refund would be right now
DELETE /bookings/{pnr}            cancel, refund, and promote the waiting list
GET    /trains/{id}/waitlist      per class, never global
```

`/health` reports Postgres and Redis **separately**. If it says
`postgres: false` the app started fine and your compose wiring is wrong —
do not go looking in the application code.

### Migrations

`migrations/` holds `.sql` files applied **in filename order** before the app
starts. They create the tables and insert sample data. A container running
`psql` over them in order is enough; you do not need a migration tool.

---

## 2. What you must write

| File | What it has to do |
|---|---|
| `Dockerfile` | Install dependencies **before** copying source, pin the base image, do not run as root. |
| `docker-compose.yml` | App + Postgres + Redis + a migration step, one `docker compose up`. |
| `.circleci/config.yml` | lint → unit tests → integration tests → secret scan → image build |
| Unit tests | For `app/fares.py`. No database, no network. |
| Integration tests | Against a real Postgres and Redis as CircleCI service containers. |

Then push your image to **your own Docker Hub account**, tagged `:1.0`.

### When it works

```bash
docker compose up --build
curl localhost:8080/health
```

```json
{"status":"ok","postgres":true,"redis":true}
```

---

## Where the marks are

`app/fares.py` is **pure logic** — plain functions over plain data, no
database and no HTTP. Start your tests there. Use pytest:
`pytest --cov=app --cov-report=term-missing`. Minimum 70%.

`promote` is the function to hammer. Build a waiting list with mixed classes and mixed party sizes, free two seats in one class, and assert on exactly who moved, who did not, and what the remaining positions were renumbered to. Then do the refund band edges: exactly 48, 24, 12 and 4 hours, and a cancellation after the train has already left.

## Why Redis is here

Two jobs. **Search caching:** a search is read hundreds of times for every time the underlying data changes - a whole class refreshing the same Mumbai-Delhi page - so the assembled result is cached for a minute and dropped the moment a hold, booking or cancellation invalidates it. **Seat holds:** each hold is its own key with a short TTL, so a passenger who abandons the payment page releases the seat by themselves. No sweeper job, no stuck inventory, and no row in Postgres that somebody has to remember to delete.

## The hard part

**The waiting list.** When a confirmed ticket is cancelled, the first waitlisted passenger must be promoted automatically - in the same transaction as the cancellation, or two people end up holding one berth. Two traps come with it. The list is **per class**: cancelling a sleeper berth must not confirm somebody waiting for AC 2 Tier. And a **party of three cannot be split** across two freed seats, so they are skipped rather than half confirmed - which means the queue is not always served strictly in order, and you must be able to explain why.

Write your answer in your README. It is worth more marks than the feature.

---

## Getting unstuck

| Symptom | Almost always |
|---|---|
| `/health` says `postgres: false` | Wrong hostname. In compose the host is the **service name**, not `localhost`. |
| Page will not load, logs fine | No `ports:` mapping, or bound to `127.0.0.1` not `0.0.0.0`. |
| `relation "..." does not exist` | Migrations did not run, or the app started before they finished. |
| Build takes minutes each time | `COPY . .` is above your dependency install. |
| CI cannot reach the database | In CircleCI service containers the host **is** `localhost` — opposite of compose. |
