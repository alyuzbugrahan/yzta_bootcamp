# Agrovision — Desktop → Web Backend Migration Plan

**Scope:** backend only. This document defines the server architecture, the API contract the
frontend will consume, and an ordered implementation plan. No frontend code is specified beyond
the wire protocol both sides must agree on.

**Target model (confirmed):** the farmer's camera is attached to *their* device. The browser
captures frames via `getUserMedia`, streams them to a central backend, the backend runs the YOLO
pipeline and streams results back. This mirrors the desktop UX but relocates the camera to the
client and the inference to a shared server.

---

## 1. What changes, and why

The desktop app is a single-process, single-user program where the camera, the model, the
pipeline state, and the database all live in one address space. Four of those assumptions break
at once when it becomes a multi-user web service.

| Desktop assumption | Where it lives | Why it breaks | Resolution |
|---|---|---|---|
| Camera is `cv2.VideoCapture(0)` | `vision/camera_manager.py:21` | No camera on the server | Delete. Frames arrive as JPEG over WebSocket. |
| One engine instance holds tracking state | `vision/inference_engine.py:77` (`self._tracks`) | Shared across users → cross-contamination | Split stateless detector from per-connection state. |
| Confidence threshold mutates the engine | `vision/inference_engine.py:729` (`set_conf_threshold`) | One user's slider changes everyone's results | `conf`/`iou` become per-call arguments. |
| One SQLite connection, `check_same_thread=False` | `data/database_handler.py:84` | Write contention, no user scoping | PostgreSQL + per-request sessions + `user_id`. |
| Frame counters are in-memory | `data/session_manager.py:20` (`_fig_counter`) | Lost on reconnect, races between workers | Derive from DB under the existing `UNIQUE(session_id, fig_seq)`. |
| Files written to local paths | `utils/path_builder.py`, `data/image_archiver.py` | Not addressable by a browser, not replica-safe | Object storage keys + authenticated read endpoint. |

**What survives nearly intact:** the candidate-finding logic (`_find_figs`), the crop/square/
unproject geometry (`_crop_and_square`, `_box_to_original`), the temporal stability tracker, the
slot/cooldown trigger logic, the NMS implementations, and the database schema shape. That is the
valuable part of this codebase and the plan preserves it verbatim where possible.

**What is deleted:** all of `ui/`, `main.py`, `control/hardware_monitor.py` (no server-side
camera to ping), `control/state_manager.py` (global app FSM becomes per-session DB state), and
`VideoProcessorWorker._annotate` (the browser draws the overlay from normalized bboxes — the
pipeline already emits `bbox` in 0–1 normalized coordinates, so this is free).

### 1.1 Four correctness issues that must be fixed during the port

These are not refactors; a naive port ships them as bugs.

1. **`batch_id` collides across users.** `sessions.batch_id` is `UNIQUE` globally and generated as
   `BATCH_{YYYYmmdd_HHMMSS}` (`data/session_manager.py:24-25`). Two farmers starting a scan in the
   same second get an `IntegrityError`. → Constraint becomes `UNIQUE(user_id, batch_id)`, and the
   session gets a separate opaque `uuid` for URLs.

2. **`SUM(decision = 'Aflatoxin')` is SQLite-only.** `data/session_dao.py:51-52` relies on SQLite
   treating a boolean as 0/1. PostgreSQL rejects it. → `COUNT(*) FILTER (WHERE decision = 'Aflatoxin')`.

3. **Absolute pixel thresholds break at other resolutions.** `min_candidate_area_px = 2500`
   (`config.ini:34`) is tuned for the desktop's 1280×720 capture. Browsers will send whatever
   `getUserMedia` and the client downscaler produce. At 640×360 the same physical fig is ~4× smaller
   in pixels and is silently filtered out. → Drop the absolute threshold; keep only the
   frame-area-ratio thresholds (`min_candidate_area_ratio`, `max_candidate_area_ratio`), which are
   already resolution-independent.

4. **Frame-count tuning assumes 30 fps.** `PRESENCE_CONFIRM_FRAMES = 3`, `COOLDOWN_FRAMES = 8`
   (`ui/video_processor_worker.py:14-15`), `stability_required`, `max_missing_frames` are all frame
   counts. At the 5–10 fps a network round-trip allows, "3 frames" goes from 100 ms to 300–600 ms and
   the cooldown from 270 ms to nearly 2 s — figs will be double-counted or missed. → Convert these to
   **durations in seconds**, resolved against the connection's measured effective fps.

   Also note the demo-mode RNG (`inference_engine.py:84`, seeded `default_rng(42)`) and its
   `time.sleep(0.03)` are instance state on the shared engine — both move to per-connection.

---

## 2. Target architecture

```
Browser (farmer's device)                    Backend (central)
─────────────────────────                    ─────────────────────────────────────
getUserMedia                                 FastAPI
  → <canvas> downscale to 640px              ├── REST  /api/v1/*   (auth, sessions, history, CSV)
  → JPEG q0.7 encode                         └── WS    /api/v1/ws/scan/{session_uuid}
  → WebSocket binary frame  ───────────────────────►
                                                  ScanConnection (one per WS)
                                                    ├── latest-frame slot (drops stale frames)
                                                    ├── TemporalStabilizer  (per-connection)
                                                    ├── SlotTracker         (per-connection)
                                                    └── session context (user, conf, fig_seq)
                                                          │
                                                          ▼
                                                  Detector (process-wide singleton)
                                                    model loaded once, called under
                                                    a bounded thread pool
                                                          │
  ◄─────────────────────────────────────────────  JSON: detections + stats + inspection events
                                                          │
                                                          ├─► PostgreSQL  (sessions, inspections)
                                                          └─► Object store (annotated-source JPEGs)
```

### 2.1 Concurrency model

Inference is CPU-bound and blocking. Both backends release the GIL during the actual compute
(ONNX Runtime and PyTorch both do), so threads are the right primitive — but the blocking call must
never run on the event loop.

- **One `Detector` per process.** Loading the model per connection would exhaust memory instantly.
- **Bounded thread pool** sized to `min(cpu_count, MAX_CONCURRENT_INFERENCES)`. Every
  `predict` goes through `anyio.to_thread.run_sync` under a `CapacityLimiter`.
- **One in-flight frame per connection.** Each `ScanConnection` holds a single "latest frame" slot.
  A frame arriving while inference is running *replaces* the pending frame rather than queueing.
  This bounds latency and makes overload degrade as frame-rate loss instead of unbounded lag —
  the same tradeoff the desktop app gets for free by being synchronous.
- **Deploy with `--workers 1`** plus internal threads, then scale horizontally with replicas.
  Multiple uvicorn workers would multiply model memory. WebSocket state is entirely per-connection,
  so replicas need no sticky-session configuration — any replica can serve any new connection.

### 2.2 Repository layout

New tree alongside the existing app. The desktop app is **frozen, not maintained in parallel** —
keeping both alive against a shared core doubles the testing surface for no benefit here.

```
backend/
├── pyproject.toml
├── Dockerfile
├── alembic/                       # migrations
└── app/
    ├── main.py                    # app factory, lifespan (model load, pool init)
    ├── config.py                  # pydantic-settings; replaces utils/config_manager.py
    ├── deps.py                    # DI: db session, current_user, detector
    ├── api/v1/
    │   ├── auth.py                # register, login, refresh
    │   ├── sessions.py            # start/stop/list/get/delete
    │   ├── inspections.py         # list, image fetch
    │   ├── exports.py             # streaming CSV
    │   ├── health.py
    │   └── ws_scan.py             # the realtime endpoint
    ├── core/
    │   ├── security.py            # argon2 hashing, JWT
    │   ├── logging.py             # structlog; replaces utils/logger.py
    │   └── errors.py
    ├── domain/                    # ← the ported vision logic, framework-free
    │   ├── models.py              # Detection, InspectionResult (from utils/dto.py)
    │   ├── candidates.py          # find_fig_candidates, crop_and_square, box_to_original
    │   ├── detector.py            # stateless model wrapper
    │   ├── stabilizer.py          # TemporalStabilizer (per-connection)
    │   ├── slots.py               # SlotTracker (per-connection)
    │   ├── demo.py                # DemoDetector (per-connection RNG)
    │   └── pipeline.py            # ScanPipeline: decode → detect → stabilize → slots
    ├── infra/
    │   ├── db/{base,models,session}.py
    │   ├── repositories/{user,session,inspection}.py
    │   ├── storage/{base,local,s3}.py
    │   └── archiver.py            # async image writer (from data/image_archiver.py)
    └── services/
        ├── scan_service.py        # ScanConnection lifecycle
        └── session_service.py     # from data/session_manager.py
```

---

## 3. Data model

```sql
-- new
users (
  id            BIGSERIAL PRIMARY KEY,
  email         CITEXT NOT NULL UNIQUE,
  password_hash TEXT   NOT NULL,
  is_active     BOOLEAN NOT NULL DEFAULT TRUE,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
)

-- ported, with additions marked ✚
sessions (
  id             BIGSERIAL PRIMARY KEY,
  uuid           UUID NOT NULL UNIQUE,                    -- ✚ opaque public identifier
  user_id        BIGINT NOT NULL REFERENCES users(id) ON DELETE CASCADE,  -- ✚
  batch_id       TEXT NOT NULL,
  device_label   TEXT,                                    -- ✚ free text, e.g. "Barn cam"
  conf_threshold REAL NOT NULL,                           -- ✚ snapshot at session start
  start_time     TIMESTAMPTZ NOT NULL,
  end_time       TIMESTAMPTZ,                             -- NULL ⇒ session open
  total_count    INTEGER NOT NULL DEFAULT 0,
  defect_count   INTEGER NOT NULL DEFAULT 0,
  CHECK (total_count >= 0),
  CHECK (defect_count >= 0),
  CHECK (defect_count <= total_count),
  UNIQUE (user_id, batch_id)                              -- ✚ was globally UNIQUE
)

inspections (
  id          BIGSERIAL PRIMARY KEY,
  session_id  BIGINT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  fig_seq     INTEGER NOT NULL,
  timestamp   TIMESTAMPTZ NOT NULL,
  decision    TEXT NOT NULL CHECK (decision IN ('Healthy','Aflatoxin')),
  confidence  REAL NOT NULL CHECK (confidence BETWEEN 0.0 AND 1.0),
  latency_ms  REAL NOT NULL DEFAULT 0.0,
  image_key   TEXT,                                       -- ✚ object-store key, was image_path
  UNIQUE (session_id, fig_seq)
)

CREATE INDEX idx_sessions_user     ON sessions (user_id, id DESC);
CREATE INDEX idx_insp_session      ON inspections (session_id);
CREATE INDEX idx_insp_decision     ON inspections (session_id, decision);
```

`fig_seq` allocation: `INSERT ... SELECT COALESCE(MAX(fig_seq),0)+1 FROM inspections WHERE
session_id = $1` inside the insert statement, retried once on unique violation. The existing
`UNIQUE(session_id, fig_seq)` is what makes this safe — keep it.

---

## 4. API contract

All REST responses are JSON. Auth is `Authorization: Bearer <access_token>`.
Every session/inspection query is scoped to `current_user` at the repository layer — ownership is
never inferred from a client-supplied id alone.

### 4.1 REST

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/api/v1/auth/register` | `{email, password}` → `{access_token, refresh_token}` |
| `POST` | `/api/v1/auth/login` | same shape |
| `POST` | `/api/v1/auth/refresh` | `{refresh_token}` → new pair |
| `GET`  | `/api/v1/me` | current user |
| `POST` | `/api/v1/sessions` | `{conf_threshold?, device_label?}` → session object incl. `uuid`, `batch_id`, `ws_url` |
| `POST` | `/api/v1/sessions/{uuid}/stop` | closes session, writes totals, returns summary |
| `GET`  | `/api/v1/sessions` | paginated list (`?limit&cursor`), newest first |
| `GET`  | `/api/v1/sessions/{uuid}` | session + summary stats |
| `DELETE`| `/api/v1/sessions/{uuid}` | cascade-deletes inspections and stored images |
| `GET`  | `/api/v1/sessions/{uuid}/inspections` | paginated inspection rows |
| `GET`  | `/api/v1/sessions/{uuid}/export.csv` | streaming CSV, same columns as desktop export |
| `GET`  | `/api/v1/inspections/{id}/image` | 302 to presigned URL, or proxied bytes |
| `GET`  | `/api/v1/model/info` | `{backend: "pt"\|"onnx"\|"demo", demo_mode, class_names, input_size}` |
| `GET`  | `/api/v1/health` | liveness + model/db/storage readiness |

Session summary payload matches `SessionDAO.get_summary` (`data/session_dao.py:42`):
`{total, aflatoxin, healthy, ratio_pct, avg_conf, avg_lat_ms, min_lat_ms, max_lat_ms}`.

CSV columns are unchanged from `session_dao.py:100`:
`Fig_ID, Batch_ID, Timestamp, Decision, Confidence, Latency_ms, Image_Path` — with `Image_Path`
carrying the API image URL rather than a local filesystem path.

### 4.2 WebSocket — `/api/v1/ws/scan/{session_uuid}`

Auth: token passed in the `Sec-WebSocket-Protocol` header or as a short-lived
`?ticket=` query param issued by `POST /sessions` (browsers cannot set headers on `WebSocket`).
Connection is rejected if the session is closed, not owned by the caller, or already has a live
connection.

**Client → server**

- Binary message: a single JPEG frame. Nothing else. Keeps the hot path allocation-free.
- Text message (JSON) for control:
  ```json
  {"type": "set_conf", "value": 0.62}
  {"type": "pause"}   {"type": "resume"}
  ```

**Server → client**

```json
// per processed frame
{"type":"frame","seq":412,"latency_ms":88.4,
 "detections":[{"class_name":"Aflatoxin","confidence":0.91,"bbox":[0.21,0.30,0.55,0.71]}],
 "stats":{"active_slots":2,"locked_slots":1,"effective_fps":7.3,"dropped":31}}

// emitted when a slot locks — one fig recorded
{"type":"inspection","fig_seq":37,"decision":"Healthy","confidence":0.88,
 "latency_ms":91.0,"timestamp":"2026-07-29T10:12:03Z","image_url":"/api/v1/inspections/9912/image"}

// running totals, so the client never recomputes them
{"type":"stats","total":37,"healthy":31,"aflatoxin":6,"ratio_pct":16.2}

// the server declined this frame — reason is "rate", "superseded" or "paused"
{"type":"dropped","reason":"rate"}

{"type":"error","code":"FRAME_TOO_LARGE","message":"..."}
```

**Every frame draws exactly one reply — `frame`, `dropped`, or `error`.** This is load-bearing:
the client below only sends its next frame once the previous one is answered, so a frame the
server silently ignored would stall it permanently. Treat `dropped` as "that one didn't count,
send the next"; it is the normal signal under load, not an error condition worth surfacing.

`bbox` is `[x1,y1,x2,y2]` normalized 0–1 — already the format the pipeline produces
(`inference_engine.py:294`), so the client scales by its own rendered video dimensions.

**Client-side capture guidance** (for the frontend team — bandwidth is the binding constraint):
downscale to 640 px on the long edge and encode at JPEG q≈0.7, giving ~40–60 KB per frame. At the
5–10 fps target that is 200–600 KB/s upstream. Sending raw 720p at 30 fps would be ~4 MB/s per
farmer and will not work on rural connections. Send the next frame only after the previous
`frame` response arrives — this self-clocks the client to whatever the server can sustain.

---

## 5. Implementation phases

Each phase is independently verifiable. Phases 1–2 have no HTTP surface and are pure-Python
testable, which is deliberate: the vision logic gets locked down by tests before any web
framework is involved.

### Phase 0 — Project skeleton *(0.5 day)*

- `backend/` tree, `pyproject.toml` (fastapi, uvicorn, pydantic-settings, sqlalchemy[asyncio],
  asyncpg, alembic, python-jose, argon2-cffi, structlog, opencv-python-headless, numpy,
  onnxruntime, ultralytics, pytest, pytest-asyncio, httpx).
- `app/config.py`: `Settings` from env, replacing the `ConfigManager` singleton
  (`utils/config_manager.py:8` — the `__new__` singleton silently ignores its `config_path`
  argument after first construction, which makes it untestable; it does not survive).
- `app/core/logging.py`: structlog JSON to stdout, with `user_id`/`session_uuid` binding.

**Done when:** `uvicorn app.main:app` serves `/api/v1/health` returning `{"status":"ok"}`.

### Phase 1 — Domain core extraction *(2–3 days)* ← highest-value phase

Port the vision logic with the state boundary corrected. **No new algorithms** — this is a
faithful move with the four fixes from §1.1 applied.

- `domain/candidates.py` ← `_find_figs`, `_crop_and_square`, `_box_to_original`. Pure functions
  taking an explicit `CandidateParams`. Absolute-pixel threshold removed (fix #3).
- `domain/detector.py` ← model loading + `.pt`/`.onnx` prediction + NMS. **`predict(frame, conf,
  iou)` — thresholds are arguments** (fix, §1 table row 3). No `self._tracks`, no `set_conf_threshold`.
- `domain/stabilizer.py` ← `_apply_temporal_stability` and friends, holding `_tracks`. Constructed
  per connection. Thresholds expressed in seconds, converted to frame counts from measured fps (fix #4).
- `domain/slots.py` ← `VideoProcessorWorker._process_slots`, holding `_slots`/`_next_slot_id`.
  Returns `list[InspectionResult]` instead of emitting Qt signals. Durations in seconds (fix #4).
- `domain/demo.py` ← `_demo_predict` with a per-connection RNG and no `time.sleep`.
- `domain/pipeline.py` — `ScanPipeline.process(frame, conf) -> FrameOutcome` composing the above.

**Tests:** golden-image tests for `find_fig_candidates`; a synthetic detection sequence asserting
the stabilizer only emits after `stability_required` consecutive hits and drops tracks after
`max_missing_frames`; a slot-tracker test asserting one fig produces exactly one `InspectionResult`
across a presence→absence→re-entry cycle; a test asserting two `ScanPipeline` instances sharing one
`Detector` produce independent track state.

**Done when:** the vision test suite passes with no import of fastapi, sqlalchemy, or PyQt.

### Phase 2 — Persistence *(1.5 days)* — **DONE**

- SQLAlchemy models per §3; Alembic initial migration.
- `repositories/`: `UserRepository`, `SessionRepository` (port of `SessionDAO` — with fix #2, the
  `FILTER` rewrite), `InspectionRepository`. Every read method takes `user_id` and filters on it.
- `services/session_service.py` ← `SessionManager`, with `fig_seq` from the DB rather than an
  in-memory counter.
- CSV generation as an async row-streaming generator; no temp files, `exports_dir` retired.

**Done when:** repository tests pass against a real Postgres (testcontainers or a CI service
container), including a test that user A cannot read user B's session by id.

**Outcome.** 59 persistence tests, all green. Ownership isolation is asserted on every read,
write, close and delete path. The Alembic migration applies and reverses, and
`test_migration_matches_the_models` fails the build on any drift between the models and the
migration.

✅ **Verified against PostgreSQL 16.14**, via `backend/docker-compose.yml`. The full suite passes
on both backends — 133 tests on PostgreSQL, 132 + 1 skip on SQLite — and prints the dialect it
ran against so a green run cannot be mistaken for coverage it did not have.

`tests/infra/test_dialect_behaviour.py` covers what SQLite alone cannot prove, and the produced
schema was inspected directly: `uuid` and `timestamp with time zone` are the native types, and
every CHECK, unique constraint and `ON DELETE CASCADE` is enforced by the database.

Two issues surfaced only once PostgreSQL was in play:

* The migration tests set `FIGION_DATABASE__URL` themselves, so they had been running on SQLite
  even when the rest of the suite was pointed at PostgreSQL — the migration path was untested on
  the real target. They now honour `FIGION_TEST_DATABASE_URL`.
* `alembic/env.py` set `render_as_batch=True` unconditionally. Batch mode rebuilds a table in
  order to alter it, which SQLite requires and PostgreSQL does not; left in place it would have
  turned routine column changes into full rebuilds of `inspections` under an exclusive lock
  during deploy. It is now SQLite-only.

**Bug found and fixed during this phase.** The `batch_id` retry and the `fig_seq` retry both
called `session.rollback()`, which unwinds the *entire* transaction rather than the failed
statement. Starting a second session in the same second would have silently destroyed the first
session and every inspection already recorded against it. Both now roll back to a `SAVEPOINT`;
`test_batch_collision_does_not_discard_earlier_work` is the regression.

### Phase 3 — REST API *(2 days)* — **DONE**

- Auth: argon2 hashing, JWT access (15 min) + refresh (30 day), `get_current_user` dependency.
- All §4.1 endpoints, with pydantic request/response schemas.
- Cursor pagination on the two list endpoints.
- OpenAPI schema published at `/docs` — this is the frontend team's handoff artifact.

**Done when:** the full session lifecycle (register → start → stop → summary → CSV) works end to
end against the API with no WebSocket involved, and authorization tests cover cross-user access on
every endpoint that takes an id.

**Outcome.** 66 API tests; 199 in total, green on PostgreSQL and SQLite alike. The lifecycle runs
end to end in `tests/api/test_lifecycle.py`, and `tests/api/test_authorization.py` drives the
cross-user matrix over every id-taking route. That matrix is self-policing:
`test_every_id_route_is_covered` reads the OpenAPI schema and fails when a route is added without
an entry, so it cannot decay into "the routes someone remembered".

**Decisions worth knowing about:**

* **PyJWT instead of python-jose**, which this plan originally named. python-jose has been
  effectively unmaintained since 2021 and carries known advisories; PyJWT is the maintained
  reference implementation. The tokens are identical.
* **Another farmer's session returns 404, not 403.** A 403 confirms the id is real, which is
  enough to enumerate how many sessions a competitor is running.
  `test_unknown_and_forbidden_ids_are_indistinguishable` asserts the two responses are
  byte-identical.
* **Starting a session while one is open returns 409**, carrying the open session's uuid. The
  desktop app could not reach this state — closing the window ended the session — but a browser
  tab closing mid-scan leaves the row open, and silently starting a second would strand the first
  with no `end_time` and no totals.
* **Login treats "no such email" and "wrong password" identically**, down to hashing a dummy
  value when no user matched, so response timing does not disclose which addresses are
  registered.
* **Refresh rotation is not revocation.** A rotated refresh token stays valid until it expires; a
  `jti` denylist is Phase 6 work, listed there.

**Two issues found while building:**

* `test_every_id_route_is_covered` initially enumerated `app.routes` and silently matched
  *nothing* — this FastAPI version keeps included routers as opaque `_IncludedRouter` wrappers,
  so the guard was passing while checking zero routes. It now reads the OpenAPI schema and
  asserts the discovered set is non-empty before comparing.
* PyJWT warned that the development signing key was 29 bytes, under the 32-byte HMAC-SHA256
  floor in RFC 7518 §3.2. A minimum length is now enforced on `secret_key` in any environment,
  alongside the existing refusal to boot outside dev with the default key.

### Phase 4 — Realtime scan *(2–3 days)* — **DONE**

- `infra/model_pool.py`: process-wide `Detector` loaded in the FastAPI lifespan, `CapacityLimiter`-
  bounded `to_thread` execution.
- `services/scan_service.py`: `ScanConnection` — latest-frame slot with stale-frame drop,
  per-connection `ScanPipeline`, effective-fps measurement, `dropped` counter.
- `api/v1/ws_scan.py`: ticket auth, ownership check, single-connection-per-session enforcement,
  the §4.2 message protocol, graceful close that finalizes the session.
- Frame validation before decode: size cap (default 2 MB), JPEG magic-byte check, decoded-dimension
  cap (default 1920×1080) — an unvalidated `cv2.imdecode` on attacker-controlled bytes is a
  memory-exhaustion vector.

**Done when:** an integration test drives a real WebSocket with a recorded frame sequence and
asserts the emitted `inspection` events match what the desktop pipeline produced on the same
footage; a second test asserts frames sent faster than inference completes are dropped rather
than queued, and latency stays bounded.

**Outcome.** 38 new tests (23 WebSocket, 15 frame-validation); 236 in total, green on PostgreSQL
and SQLite. `test_frames_are_dropped_rather_than_queued` and `test_latency_stays_bounded_under_load`
cover the backpressure criterion.

⚠️ **The recorded-footage comparison is NOT done.** No UV footage ships with the repository, so
the WebSocket tests drive synthesised frames against a scripted detector and assert the *slot
semantics* the desktop pipeline had — one fig produces exactly one record, a brief dropout does
not produce a second — end to end through the socket, the thread pool and the database. That is
strictly weaker than the criterion as written. Comparing fig counts against the desktop app on
the same clips remains the open item, and it is the only way to validate the retuned timing
gates from §1.1. It needs footage from whoever owns the rig.

**Design decisions:**

* **Single-slot mailbox, not a queue.** A frame arriving while inference runs *replaces* the one
  waiting. The desktop worker got this free — a slow frame just meant the next `read_frame`
  returned a newer image — but frames now arrive on the client's schedule. Queueing would let a
  farmer sending 15 fps into an 8 fps pipeline build an unbounded backlog, with the on-screen
  boxes drifting further behind the belt every second. Dropping keeps the display honest.
* **Disconnect does not close the session.** The plan said "graceful close that finalizes the
  session"; that is wrong for mobile. A dropped connection is normal, and finalising totals on
  it would end a farmer's harvest because they walked behind a barn. Reconnecting resumes the
  same session with its `fig_seq` series intact — which is what moving that counter into the
  database in Phase 2 was for. `POST /sessions/{uuid}/stop` remains the only way to finalise.
* **Short-lived WebSocket tickets.** A browser cannot set an `Authorization` header on a
  WebSocket handshake, so the credential must travel in the URL — where proxies log it and
  browsers keep it in history. A 15-minute access token there is a real leak; a 60-second
  ticket bound to one session via a `sid` claim is not.
* **Decompression-bomb defence.** Dimensions are parsed from the JPEG header *before* any
  decode. Byte length is not a defence — the entire point of a bomb is that the compressed form
  is small. `test_decompression_bomb_is_refused_before_decoding` uses a sub-1 KB payload
  declaring 25000×25000, which would allocate roughly 1.9 GB if it reached `cv2.imdecode`.

**Two issues the guards caught:**

* Adding `POST /sessions/{uuid}/ticket` without updating the authorization matrix — exactly the
  decay `test_every_id_route_is_covered` exists to prevent. It failed the build; the route is
  now covered.
* A WebSocket test hung for the full 60-second timeout because `TestClient.receive_json` blocks
  with no timeout and the test asked for more messages than the rate gate would ever produce.
  `pytest-timeout` is now configured at 60 s so this fails fast rather than stalling a run.

### Phase 5 — Image storage *(1 day)* — **DONE**

- `infra/storage/`: `StorageBackend` protocol, `LocalStorage` (dev), `S3Storage` (prod, MinIO-compatible).
- Key scheme: `u{user_id}/{batch_id}/fig_{seq:04d}_{decision}.jpg` — the `PathBuilder`
  (`utils/path_builder.py:12`) scheme with the date folder replaced by user scoping.
- `infra/archiver.py`: `ImageArchiver` ported to an `asyncio.Queue` with the same bounded-queue,
  drop-and-warn behavior (`data/image_archiver.py:33`). Backpressure must never stall the scan loop.
- `GET /inspections/{id}/image`: ownership check, then presigned redirect (S3) or streamed bytes (local).
- `DELETE /sessions/{uuid}` removes stored objects alongside the rows.

**Done when:** a full scan session produces retrievable images, and one user 404s on another's image id.

**Outcome.** 42 new tests; 282 in total, green on PostgreSQL and SQLite. Both acceptance
criteria are met by `test_a_scanned_fig_produces_a_retrievable_image` and
`test_another_farmer_cannot_fetch_the_image`. **S3 is verified against real MinIO**, added to
`docker-compose.yml` — including that a presigned URL actually fetches the object and that it
stops working once expired.

**Design decisions:**

* **The archived bytes are the client's original JPEG, not a re-encode.** The desktop app had to
  encode, because it held a raw camera array (`cv2.imwrite`, quality 90). Frames now arrive
  already compressed, so archiving costs nothing on the hot path — and the stored image is
  byte-identical to what the model classified, which is what makes it usable as evidence for a
  disputed reading. `test_the_stored_image_is_the_frame_the_model_saw` pins this.
* **The image key is written in a second step.** It embeds `fig_seq`, which the database
  allocates during the insert, so there is no key to store until the row exists.
* **The CSV links to `/inspections/{id}/image`, not the storage key.** That endpoint re-checks
  ownership on every fetch; a raw bucket key in a spreadsheet is a bare pointer, and a
  spreadsheet is exactly the kind of thing that gets forwarded to a buyer. *This also fixed a
  latent bug:* the Phase 3 export pointed at `/api/v1/images/{key}`, a route that never existed.
* **`FIGION_STORAGE__ENABLED=false` keeps decisions and statistics while storing nothing.** The
  cheapest answer to the retention question if evidence is not needed — see §7.2, still open.

**Security bug found and fixed.** `validate_key` rejected traversal with a character-class
regex that permits `.` — so `..` matched as an ordinary segment and the guard did nothing.
`LocalStorage` was still safe via its second `is_relative_to` check, but `S3Storage` relies on
this function alone. `..`, `.` and empty segments are now rejected explicitly, before the
character test. Found by `test_unsafe_keys_are_rejected`, which was written to assert the
behaviour the docstring already claimed.

### Phase 6 — Hardening *(1.5 days)* — **DONE**

- Rate limits: auth endpoints per IP; WebSocket connections per user; server-side ingest fps cap.
- Request body size limits; CORS restricted to known frontend origins; security headers.
- Structured error responses with stable `code` values (the frontend switches on these).
- Load test: N concurrent scanning connections against one replica, to find the real
  `MAX_CONCURRENT_INFERENCES` and document the per-replica user capacity.

**Done when:** load-test numbers are recorded in this doc and no endpoint is unauthenticated
except health, auth, and the OpenAPI schema.

**Outcome.** 317 tests passing. Rate limits (per-IP on credentials, per-user WebSocket
concurrency, ingest fps cap), body-size and security-header middleware, a CORS wildcard guard,
and account-wide token revocation via `POST /auth/logout-all`.

**Load results** — `backend/tools/loadtest_scan.py`, 28-core host, single uvicorn worker,
SQLite, demo detector, 640×480 q70 (7 KB/frame), server capped at 12 fps ingest:

| Clients | Aggregate fps | Per-client fps | p50 | p95 | Upstream |
|---|---|---|---|---|---|
| 1 | 11.8 | 11.8 | 1 ms | 2 ms | 83 KB/s |
| 4 | 46.4 | 11.6 | 1 ms | 1 ms | 325 KB/s |
| 8 | 89.8 | 11.2 | 1 ms | 2 ms | 629 KB/s |
| 16 | 158.0 | 9.9 | 2 ms | 3 ms | 1.1 MB/s |
| 32 | 296.6 | 9.3 | 4 ms | 5 ms | 2.1 MB/s |

⚠️ **These are not capacity numbers.** No model file exists in this repository, so the run used
the demo detector: the figures measure transport, decode and pipeline overhead — a floor. Real
capacity is dominated by YOLO inference (tens to >100 ms per frame on CPU), which is one to two
orders of magnitude above the ~1 ms measured here. What the sweep does establish is that nothing
in the transport collapses under fan-out: per-client throughput degrades gently (11.8 → 9.3 fps
across a 32× increase) and p95 latency stays in single-digit milliseconds. **Re-run against the
real weights before sizing a deployment**; `MAX_CONCURRENT_INFERENCES` cannot be chosen
meaningfully until then.

**Two bugs found by the load harness — neither visible to the test suite:**

* **Silently dropped frames deadlocked well-behaved clients.** §4.2 instructs clients to send
  the next frame only once the previous one is answered, but a frame refused by the rate gate
  produced no reply at all. The harness stalled after eight frames; a real browser would have
  frozen identically. The server now acknowledges every frame it declines with
  `{"type": "dropped", "reason": ...}`. This is a **protocol addition the frontend must
  handle** — see §4.2.
* **Writes committed after the response was sent.** Since FastAPI 0.106 the exit half of a
  `yield` dependency runs *after* the response, so `get_db`'s commit landed after the caller
  already held its reply. Registration returned a token whose user row was not yet durable, and
  the client's next request failed with "token subject no longer exists". Every existing test
  happened to tolerate the lag; only a client acting immediately on the response exposed it.
  `get_db` no longer commits — write handlers commit explicitly, before responding.

A third was caught while writing the migration: the revocation column was first modelled as
`NOT NULL DEFAULT CURRENT_TIMESTAMP`, which migrates cleanly against an empty table and fails
against a populated one, since SQLite rejects a non-constant default in `ADD COLUMN`. It is now
a nullable generation counter, and `test_migrations_apply_to_a_table_that_already_has_rows`
covers the case.

**Deliberately not per-token revocation.** `logout-all` bumps a `users.token_generation`
counter that every replica reads; a `jti` denylist would need shared state. Timestamp-based
cutoffs were tried first and abandoned: JWT `iat` has one-second resolution, so a token minted
in the same second as a revocation is indistinguishable from one minted just before it, and
stamping fresh tokens forward to compensate makes PyJWT reject them as not-yet-valid. Integers
have no resolution to lose.

**Rate limits are per-process.** With N replicas the effective ceiling is N×. Acceptable while
the deployment is one worker per replica; Redis is the change to make when that stops being
true.

### Phase 7 — Deployment *(1 day)* — **DONE (unverified)**

- Multi-stage `Dockerfile` (`opencv-python-headless` — the GUI build pulls X11 libs that will not
  exist in the container); model baked into the image or mounted.
- `docker-compose.yml`: api + postgres + minio for local development.
- CI: lint, type-check, test, build, `alembic upgrade head` on deploy.
- `/health` wired to readiness probes; structured logs shipped.

**Done when:** `docker compose up` gives a working backend from a clean checkout.

**Outcome.** Multi-stage `Dockerfile` (build tools stay in the builder stage), an entrypoint that
applies migrations then `exec`s the server so SIGTERM reaches uvicorn rather than a shell,
`docker-compose.yml` with api + postgres + minio, and a GitHub Actions workflow that lints, tests
against **real** PostgreSQL and MinIO service containers, builds the image and smoke-tests that
it boots and serves.

⚠️ **`docker compose up` has not been executed.** Docker Desktop's WSL integration was disabled
throughout this session, so the Dockerfile, compose file and workflow are written and statically
validated — the YAML parses and the service graph resolves — but never built or run. **The
acceptance criterion for this phase is therefore not met.** Treat the container as unproven
until someone runs `docker compose up --build` once; everything above it in the stack is covered
by the test suite.

Two deployment details worth knowing:

* **CI fails if PostgreSQL or MinIO were skipped.** The storage and dialect tests skip
  themselves when their endpoint is absent, so a broken service container would otherwise
  produce a green run that proved nothing. A second step greps for `SKIPPED` and fails the job.
* **Migrations run in the entrypoint, which is single-replica-safe only.** Concurrent
  `alembic upgrade` runs race on the version table; with several replicas, run migrations as a
  separate job and set `FIGION_RUN_MIGRATIONS=0`.

### Phase 8 — Batch reporting — **DONE**

Added after the original plan at the user's request: reports covering figs through the belt,
aflatoxin levels, healthy product, and analysis of the model's own output.

- `app/domain/report.py` — throughput, per-class breakdown, confidence histogram and
  percentiles, as pure functions over plain values.
- `GET /sessions/{uuid}/report` — JSON.
- `GET /sessions/{uuid}/report.pdf` — server-rendered PDF (ReportLab), for sending to a buyer.
- `GET /reports/range` — totals across a date window, aggregated in SQL.

**"AI analysis" is model-derived statistics, not a language model** — confirmed with the user
before building. Mean and median confidence, a confidence histogram, per-class means and minima,
latency percentiles, and a count of figs below a review threshold that a human should recheck.
No external service, no API key, no per-report cost, and it works offline. The `notes` field
holds fixed-threshold observations ("20% of figs scored below 70% confidence — check UV
lighting"), which is mechanical, not generated prose.

**Fig weight is a query parameter, not a column.** The desktop app had this field
(`main_window.py:287`) and never persisted it — §7 below flagged deciding its fate. It varies by
variety and drying, so the caller supplies it per request and the report returns an estimated
mass; omitting it returns null rather than a confidently wrong number.

**Percentiles are computed in Python, not SQL.** SQLite has no percentile function, and keeping
every query dialect-agnostic has already caught one portability bug here. The cost is loading a
session's rows, and a batch is bounded by one conveyor run.

**PDF fonts.** ReportLab's built-in fonts are Latin-1, which lacks Turkish ğ, ı, ş and İ — a
device label would render as blanks in the document handed to a buyer. The image installs
`fonts-dejavu-core` and the renderer embeds it; when the font is absent it falls back to
Helvetica rather than failing the download. Both paths were exercised.

**Two bugs found while wiring it up**, both caught by the tests:

* `build_throughput` subtracted a naive `start_time` from an aware `now`. SQLite has no
  timestamp type and returns naive datetimes where PostgreSQL returns aware ones, so this would
  have worked in production and crashed on every developer's machine. Timestamps are normalised
  on entry now.
* The response schema used `report.throughput.__dict__`, which does not exist on a
  `slots=True` dataclass.

The Phase 3 authorization-matrix guard also did its job: it failed the moment the two new
session-id routes existed without entries in the table.

**Total: roughly 12–14 working days.**

---

## 6. Risks

| Risk | Impact | Mitigation |
|---|---|---|
| Network round-trip makes detection feel sluggish vs. desktop | Core UX regression | Self-clocking client (§4.2), latency surfaced in every `frame` message so the UI can show it honestly. Accept 5–10 fps as the design point rather than pretending 30 is reachable. |
| Inference cost per user is high; CPU-only ONNX at 640² is ~50–150 ms | Few concurrent users per replica | Measure in Phase 6 before promising capacity. The candidate-finder gate (`inference_engine.py:324` — skips YOLO entirely when no fig-shaped contour is present) already suppresses most inference calls and should be preserved carefully; it is the main throughput lever. |
| Timing constants retuned for lower fps change detection behavior | Silent accuracy regression | Phase 1 tests pin behavior on recorded footage; compare counts against the desktop app on the same clips before cutover. |
| Uploaded frames are attacker-controlled input to OpenCV | Memory exhaustion / DoS | Validation before decode (Phase 4), plus per-connection and per-user limits. |
| Varying client cameras and lighting vs. the fixed UV rig | Model accuracy drops in the field | Out of backend scope, but the API should record `device_label` and preserve source images so misclassifications are diagnosable. Flag to the model owner early. |

---

## 7. Open items for the team

1. **Model distribution** — is `models/final_model.pt` committed, or fetched at build time? It is
   absent from the repo today and the app silently falls back to demo mode
   (`inference_engine.py:102`). Production must fail loudly instead: demo mode should require an
   explicit `FIGION_ALLOW_DEMO=1`.
2. **Retention** — source images at ~50 KB × thousands of figs per farmer per session accumulate
   quickly. Needs a retention policy before Phase 5 ships.
3. **Fig weight** (`main_window.py:287`) was a UI-only field never persisted. If the kg totals
   matter to farmers, it belongs on the session row; decide before Phase 2 freezes the schema.
