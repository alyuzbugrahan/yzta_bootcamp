# Agrovision — Desktop to Web Migration: Scope of Work

What was built, why each decision was made, what was found along the way, and what is still
unproven. Companion documents: [`WEB_MIGRATION_PLAN.md`](WEB_MIGRATION_PLAN.md) (the phase plan
with per-phase detail) and [`FRONTEND_API.md`](FRONTEND_API.md) (the client integration guide).

---

## 1. Starting point and goal

**Before:** a PyQt6 desktop application. One process on one machine, wired to one UV camera, one
operator, a local SQLite file, and images written to a folder on that machine's disk. The vision
pipeline was reachable only through a Qt widget tree, so none of it had tests.

**After:** a FastAPI backend serving many farmers. Each captures frames in their own browser and
streams them over a WebSocket; the server runs the YOLO pipeline and streams back detections.
Sessions, figs and images are stored per user, and every batch can be exported as CSV, JSON or a
PDF report.

**Confirmed with the user before designing:** farmers connect their own camera through the web
app, and detection should behave as it did on the desktop. That answer decided the whole
architecture — browser-captured frames, server-side inference, many independent users.

### Totals

| | |
|---|---|
| Application code | 6,114 lines across 54 files |
| Test code | 5,072 lines across 36 files |
| Tests | **378 passing**, 7 skipped |
| Migrations | 2 |
| Phases | 8 (7 planned + reporting, added later) |

Test distribution: domain 100 · infrastructure 102 · API 151 · services 16 · core 16.

---

## 2. Architecture

```
Browser (farmer's device)                Backend (central)
─────────────────────────                ──────────────────────────────────────
getUserMedia                             FastAPI
 → canvas downscale to 640px             ├── REST  /api/v1/*   auth, sessions, reports, CSV
 → JPEG q0.7                             └── WS    /api/v1/ws/scan/{uuid}
 → WebSocket binary  ──────────────────────►
                                            ScanConnection (one per socket)
                                              ├── single-slot mailbox (drops stale frames)
                                              ├── TemporalStabilizer   ← per connection
                                              ├── SlotTracker          ← per connection
                                              └── session context
                                                    │
                                                    ▼
                                            Detector (one per process)
                                              model loaded once, called under
                                              a bounded thread pool
                                                    │
 ◄──────────────────────────────────────────  JSON: detections, stats, inspections
                                                    │
                                                    ├─► PostgreSQL
                                                    └─► S3 / MinIO (source frames)
```

**Layering.** `app/domain/` holds the ported vision logic and is framework-free — a test asserts
it imports no FastAPI, SQLAlchemy or Qt. `app/infra/` holds persistence, storage and the model
pool. `app/api/` is the HTTP and WebSocket surface. `app/services/` orchestrates.

---

## 3. What was ported, and what had to change

### Preserved as-is

The contour-based fig finder, the crop/square/unproject geometry, both NMS implementations, the
temporal stability tracker, the slot/cooldown trigger logic, the database schema shape and its
CHECK constraints, and the CSV column format. This is the valuable part of the original codebase
and it survived the move intact.

### Not carried into the port

These have no counterpart in `backend/`. **They were not removed from the repository** — the
desktop application is untouched and still runs; see §9.

`ui/` and `main.py` (Qt window and splash screen), `control/hardware_monitor.py` (no server-side
camera to ping), `control/state_manager.py` (a global FSM becomes per-session state), and
`VideoProcessorWorker._annotate` — the browser draws the overlay now.

### Forced changes

| Desktop assumption | Why it broke | Resolution |
|---|---|---|
| `cv2.VideoCapture(0)` | No camera on a server | Frames arrive as JPEG over WebSocket |
| One engine holds `self._tracks` | Shared across users → contamination | Stateless detector + per-connection state |
| Confidence slider mutates the engine | One user's slider changes everyone's results | `conf`/`iou` are per-call arguments |
| One SQLite connection, `check_same_thread=False` | Write contention, no user scoping | PostgreSQL, per-request sessions, `user_id` everywhere |
| `_fig_counter` in memory | Lost on reconnect | Allocated by the database |
| Files under `data/images/` | Not addressable by a browser | Object storage keys + authenticated endpoint |

---

## 4. Correctness issues found and fixed

Fourteen defects were identified. Four were spotted while reading the original code; the rest
surfaced during implementation, and several would only ever have failed in production.

### Found by reading the desktop code

1. **`batch_id` collided across users.** Globally `UNIQUE` and derived from a second-resolution
   timestamp — two farmers pressing start in the same second would hit an `IntegrityError`.
   Uniqueness is now scoped per user.
2. **`SUM(decision = 'Aflatoxin')` is SQLite-only.** Relies on SQLite treating a boolean as 0/1;
   PostgreSQL rejects it outright. Now `COUNT(*) FILTER (WHERE …)`.
3. **Absolute pixel thresholds break at other resolutions.** `min_candidate_area_px = 2500` was
   calibrated for a fixed 1280×720 rig. Browser frames are smaller, so the same physical fig
   would be filtered out silently. Only resolution-independent ratio thresholds remain.
4. **Frame-count timings assumed 30 fps.** `PRESENCE_CONFIRM_FRAMES`, `COOLDOWN_FRAMES` and
   friends are frame counts. Over a network at 5–10 fps the cooldown stretches from 270 ms to
   ~1.6 s. See §5.

### Found during implementation

5. **Transactions committed after the response was sent.** Since FastAPI 0.106 the exit half of
   a `yield` dependency runs *after* the response. Registration handed back a token whose user
   row was not yet durable, and the client's next request failed with "token subject no longer
   exists". Every existing test tolerated the lag; only the load harness, acting immediately on
   a response, exposed it. Write handlers now commit explicitly before responding.
6. **Silently dropped frames deadlocked well-behaved clients.** The protocol tells clients to
   send the next frame only once the previous is answered, but a frame refused by the rate gate
   produced no reply at all. The harness stalled after eight frames; a real browser would freeze
   identically. Every frame now draws exactly one reply.
7. **Retry paths rolled back the whole transaction.** Both the `batch_id` and `fig_seq` retries
   called `session.rollback()`, which unwinds everything pending — starting a second session in
   the same second would have silently destroyed the first session *and every fig recorded
   against it*. Both use `SAVEPOINT` now.
8. **`render_as_batch` applied to PostgreSQL.** Batch mode rebuilds a table to alter it, which
   SQLite requires and PostgreSQL does not. Left in place, routine column changes would have
   become full rebuilds of `inspections` under an exclusive lock during deploy. Now SQLite-only.
9. **The authorization matrix guard checked nothing.** It enumerated `app.routes` looking for
   session-id routes, but this FastAPI version keeps included routers as opaque wrappers — it
   matched zero routes and passed. It now reads the OpenAPI schema and asserts the discovered
   set is non-empty before comparing.
10. **Migration tests silently ran on SQLite.** They set the database URL themselves, so
    pointing the suite at PostgreSQL left the migration path untested on the real target.
11. **A NOT NULL column with a `CURRENT_TIMESTAMP` default.** Migrates cleanly against an empty
    table and fails against a populated one — SQLite rejects non-constant defaults in
    `ADD COLUMN`. There is now a test that migrates a table containing rows.
12. **Naive vs. aware datetimes.** The report subtracted a naive `start_time` from an aware
    `now`. SQLite returns naive datetimes where PostgreSQL returns aware ones, so this would
    have worked in production and crashed on every developer's machine.
13. **A 29-byte JWT signing key**, below the 32-byte HMAC-SHA256 floor in RFC 7518 §3.2. Caught
    by a PyJWT warning; a minimum is now enforced.
14. **`__dict__` on a `slots=True` dataclass** in the report serialiser.

---

## 5. Design decisions worth knowing

### Sample-and-time gates

The single most consequential change. Neither naive conversion of the desktop's frame counts
works over a network:

- Keeping frame counts stretches every gate 3–6× in wall-clock time.
- Converting to bare durations collapses them — at 8 fps the 125 ms between frames already
  exceeds a 100 ms threshold, so *one* sighting would confirm a fig and the anti-flicker filter
  would be gone entirely.

Each gate therefore carries **both** a sample floor and a time floor and opens only when both
are met. At 30 fps the duration binds and behaviour matches the desktop; at 8 fps the sample
floor binds and the gate takes longer in wall-clock terms but sees the same evidence. Trading
latency for evidence is the correct direction — the alternative is counting figs the model
glimpsed once.

**Consequence for the product:** a fig must remain in view roughly **4× longer** than on the
desktop rig. Each fig that is counted is counted correctly; fewer are processed per minute. A
conveyor tuned to the desktop app needs slowing.

### Frames are dropped, never queued

Each connection holds one frame slot. A frame arriving during inference *replaces* the pending
one. Queueing would let a farmer sending 15 fps into an 8 fps pipeline build an unbounded
backlog, and the boxes on screen would drift further behind the belt every second. Dropping
keeps the display honest at the cost of skipped frames — exactly what the sample floors tolerate.

### One model per process

The detector is loaded once in the FastAPI lifespan and shared; inference runs on a bounded
thread pool (both ONNX Runtime and PyTorch release the GIL). Deploy with **one uvicorn worker**
and scale with replicas — a second worker doubles model memory for no throughput gain.
Connection state is per-socket, so replicas need no sticky sessions.

### Security posture

- **404, not 403**, for another user's session. A 403 confirms the id is real, which is enough to
  enumerate. The two responses are asserted byte-identical.
- **Login is timing-equalised**: a dummy hash is verified when no user matches, so response time
  does not disclose which emails are registered.
- **Token type is a claim.** A refresh token cannot be used as an access token or vice versa,
  which is what keeps the 15-minute access lifetime meaningful.
- **Revocation is a generation counter**, not a timestamp or a `jti` denylist. Timestamps cannot
  work — JWT `iat` has one-second resolution, and stamping fresh tokens forward makes PyJWT
  reject them as not-yet-valid. A denylist would need shared state across replicas; a column
  does not.
- **WebSocket tickets** last 60 seconds and name one session, because a browser cannot set an
  `Authorization` header on a handshake and the credential ends up in proxy logs.
- **Frame dimensions are parsed from the JPEG header before decoding.** A 40 KB payload can
  declare 25000×25000 and expand to gigabytes; byte length alone is no defence.
- **Demo mode must be asked for.** The desktop app silently simulated results when the model was
  missing; the server refuses to boot unless `FIGION_MODEL__ALLOW_DEMO` is set.

### Reporting: "AI analysis" is statistics, not a language model

Confirmed with the user before building. The report derives everything from predictions already
stored: mean and median confidence, a confidence histogram, per-class means and minima, latency
percentiles, and a count of figs below a 70% review threshold that a human should recheck. No
external service, no API key, no per-report cost, works offline. The `notes` field holds
fixed-threshold observations, not generated prose.

Fig weight — a desktop UI field that was never persisted — is a **query parameter**, not a
column. It varies by variety and drying, so the caller supplies it and an omitted value yields
`null` rather than a confidently wrong mass.

---

## 6. Phase-by-phase delivery

| Phase | Delivered | Status |
|---|---|---|
| 0 · Skeleton | App factory, env-driven settings, structured logging | ✅ |
| 1 · Domain core | Vision pipeline extracted framework-free, 100 tests | ✅ |
| 2 · Persistence | PostgreSQL, repositories, Alembic, ownership scoping | ✅ verified on PostgreSQL 16.14 |
| 3 · REST API | Auth, sessions, CSV export, authorization matrix | ✅ |
| 4 · Realtime | WebSocket scanning, inference pool, backpressure | ✅ |
| 5 · Storage | S3/MinIO backend, async archiver, image endpoint | ⚠️ MinIO path unverified |
| 6 · Hardening | Rate limits, security headers, revocation, load harness | ✅ |
| 7 · Deployment | Dockerfile, compose, CI workflow | ⚠️ **never built or run** |
| 8 · Reporting | JSON + PDF batch reports, date-range aggregate | ✅ |

---

## 7. Verification

**What is proven.** 378 tests pass. The full suite has been run against **real PostgreSQL
16.14**, and the produced schema inspected directly — `uuid` and `timestamp with time zone` are
the native types, and every CHECK, unique constraint and `ON DELETE CASCADE` is enforced by the
database. The suite prints the dialect it ran against, so a green run cannot be mistaken for
coverage it did not have.

Load testing: 32 concurrent scanning clients on one replica held 9.3 fps each with 5 ms p95
latency, degrading gently from 11.8 fps at a single client.

### What is not proven

1. **The container has never been built or run.** Docker Desktop's WSL integration was disabled
   for most of this work, so the `Dockerfile`, `docker-compose.yml` and CI workflow are written
   and statically validated but unexecuted. **Phase 7's acceptance criterion is not met.** Run
   `docker compose up --build` once before trusting it.
2. **MinIO/S3 storage is untested.** Six storage tests skip without `FIGION_TEST_S3_ENDPOINT`.
   Skipped is not passed — CI is configured to fail if they skip, but CI has not run either.
3. **The load figures are a floor, not a capacity.** No model file ships with this repository, so
   the sweep used the demo detector: ~1 ms per frame against tens-to-hundreds for real YOLO on
   CPU. What it establishes is that the transport does not collapse under fan-out.
   `MAX_CONCURRENT_INFERENCES` cannot be chosen until someone re-runs it against real weights.
4. **Detection accuracy was never compared against the desktop app.** No UV footage ships with
   the repository. The tests pin *behaviour* — one fig produces exactly one record, a dropout
   does not produce a second — but not agreement with the original on real images. The retuned
   timing constants in particular should be checked against recorded footage before production.
5. **The `.pt` and `.onnx` paths suppress overlapping boxes differently.** ONNX applies per-crop
   and global NMS; `.pt` applies neither across crops, so two overlapping padded candidates can
   each report the same fig. This asymmetry exists in the desktop code today and was deliberately
   preserved rather than silently changed — unifying it would alter detection counts and needs
   the footage comparison above.

---

## 8. Open items

| Item | Why it matters | Owner |
|---|---|---|
| Run `docker compose up --build` | Phase 7 acceptance is unmet until then | Backend |
| Run CI once | Would exercise the PostgreSQL + MinIO + image-build path end to end | Backend |
| Re-run load test with real weights | Real capacity is unknown; needed to size deployment | Backend + model owner |
| Compare detection against recorded footage | The retuned timing constants are unvalidated against reality | Model owner |
| Decide the `.pt`/`.onnx` NMS asymmetry | Inherited inconsistency, deliberately preserved | Model owner |
| Image retention policy | ~50 KB × thousands of figs per farmer per session accumulates fast | Product |
| Redis-backed rate limits | Current limits are per-process; N replicas means an N× ceiling | Backend, before scaling out |
| Refresh-token rotation | `logout-all` works; per-token revocation needs shared state | Backend |

### The constraint that is not a code problem

The model was trained on **UV-illuminated** figs — that is the premise of the whole system. A
farmer pointing an ordinary webcam at figs in daylight feeds the model a completely different
image distribution. The pipeline will run, return confident-looking numbers, and be wrong.

**Web works identically to desktop for farmers who have a UV lamp.** This needs to be a stated
product requirement, surfaced before the first scan, and it is worth confirming with whoever
owns the model. It also changes who the product is for.

One further difference no hardware can fix: the desktop app ran fully offline, and this does not.
Inference is server-side by design, so scanning stops when the connection drops — and farms are
often where connectivity is worst. If offline operation matters, that is a different product
shape (local install or on-device inference), and better known now than after launch.

---

## 9. Repository layout

```
backend/
├── app/
│   ├── domain/      vision logic — framework-free, enforced by test
│   ├── api/v1/      auth, sessions, exports, images, reports, ws_scan, health
│   ├── infra/       db, repositories, storage, model pool, archiver
│   ├── core/        security, errors, logging, rate limiting, middleware
│   └── services/    scan connections, sessions, CSV, PDF, frame codec
├── tests/           domain · infra · api · services · core
├── alembic/         2 migrations
├── tools/           loadtest_scan.py
├── docker/          entrypoint, database init
├── Dockerfile · docker-compose.yml
└── README.md

docs/
├── WEB_MIGRATION_PLAN.md   phase plan, per-phase outcomes and findings
├── FRONTEND_API.md         client integration guide
└── PROJECT_SCOPE.md        this document

.github/workflows/backend.yml
```

The original desktop application is untouched and frozen. It was not maintained in parallel
against a shared core — that would have doubled the testing surface for no benefit here.
