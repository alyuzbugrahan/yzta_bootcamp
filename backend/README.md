# AgroVision Backend

Web backend for the AgroVision aflatoxin detection system. See
[`../docs/WEB_MIGRATION_PLAN.md`](../docs/WEB_MIGRATION_PLAN.md) for the full architecture and
phase plan.

**Status:** All phases complete — 378 tests passing. Skeleton, domain core, persistence, REST
API, realtime scanning, image storage, hardening, deployment artifacts, and batch reporting.

⚠️ The container has **never been built or run** — Docker was unavailable throughout
development, so the `Dockerfile`, `docker-compose.yml` and CI workflow are written and
statically validated but unproven. Run `docker compose up --build` once before trusting them.

Every endpoint in §4.1 of the plan now exists. A farmer can register, open a session, stream
frames from their browser, get live detections, and retrieve the archived image behind every
recorded fig, then pull a batch report as JSON or PDF. **`/docs` is the frontend handoff
artifact.**

## Setup

Debian/Ubuntu Python ships without `pip`, so either install `python3-venv` system-wide or
bootstrap into the project venv:

```bash
sudo apt install -y python3-venv          # preferred
python3 -m venv .venv && .venv/bin/python -m pip install -e '.[dev]'
```

Without sudo:

```bash
python3 -m venv --without-pip .venv
curl -sS https://bootstrap.pypa.io/get-pip.py | .venv/bin/python
.venv/bin/python -m pip install -e '.[dev]'
```

Inference backends are optional extras, so the domain suite runs without them:

```bash
.venv/bin/python -m pip install -e '.[dev,onnx]'    # or '.[dev,torch]'
```

## Running

```bash
.venv/bin/python -m pytest tests -q
.venv/bin/ruff check app tests alembic
.venv/bin/python -m alembic upgrade head

AGROVISION_MODEL__ALLOW_DEMO=1 .venv/bin/uvicorn app.main:app --reload
```

`GET /api/v1/health` and `GET /api/v1/model/info` are live; OpenAPI is at `/docs`.

Deploy with **one** uvicorn worker and scale with replicas — the model is loaded once per
process, so multiple workers multiply its memory. Connection state is per-WebSocket, so
replicas need no sticky-session configuration.

## Configuration

Environment variables, prefixed `AGROVISION_`, nested with `__`. See `app/config.py`.

| Variable | Default | Notes |
|---|---|---|
| `AGROVISION_MODEL__PATH` | `models/final_model.pt` | `.pt` or `.onnx`; siblings are tried as fallback |
| `AGROVISION_MODEL__ALLOW_DEMO` | `false` | Without this, a missing model **refuses to boot** |
| `AGROVISION_MODEL__CONF_THRESHOLD` | `0.50` | Per-session default; clients may override |
| `AGROVISION_VISION__MIN_AREA_RATIO` | `0.006` | Resolution-independent, unlike the desktop's pixel count |
| `AGROVISION_CORS_ORIGINS` | `[]` | JSON list of frontend origins |
| `AGROVISION_STORAGE__BACKEND` | `local` | `local` or `s3` (MinIO-compatible) |
| `AGROVISION_STORAGE__ENABLED` | `true` | `false` keeps decisions but stores no images |
| `AGROVISION_STORAGE__BUCKET` | `agrovision-images` | s3 only |
| `AGROVISION_STORAGE__ENDPOINT_URL` | *(empty)* | Set for MinIO; empty means real AWS |
| `AGROVISION_DATABASE__URL` | `sqlite+aiosqlite:///./agrovision.db` | PostgreSQL is the deployment target |
| `AGROVISION_AUTH__SECRET_KEY` | dev placeholder | **Required outside dev**; min 32 bytes |
| `AGROVISION_AUTH__ACCESS_TTL_MINUTES` | `15` | Access token lifetime |
| `AGROVISION_AUTH__REFRESH_TTL_DAYS` | `30` | Refresh token lifetime |
| `AGROVISION_SECURITY__AUTH_ATTEMPTS` | `10` | Credential attempts per IP per window |
| `AGROVISION_SECURITY__MAX_CONNECTIONS_PER_USER` | `3` | Simultaneous scan sockets |
| `AGROVISION_SECURITY__TRUST_PROXY_HEADERS` | `false` | Only enable behind a proxy that rewrites `X-Forwarded-For` |
| `AGROVISION_SECURITY__HSTS` | `false` | Enable when terminating TLS |
| `AGROVISION_ENVIRONMENT` | `dev` | `staging`/`prod` refuse to boot on the default signing key |

## API

Auth is `Authorization: Bearer <access_token>`; `POST /api/v1/auth/register` and `/auth/login`
both return an access/refresh pair.

| Method | Path | Notes |
|---|---|---|
| `POST` | `/api/v1/auth/register` | 409 `EMAIL_TAKEN` if taken |
| `POST` | `/api/v1/auth/login` | 401 `INVALID_CREDENTIALS` |
| `POST` | `/api/v1/auth/refresh` | Refresh tokens only — an access token is rejected |
| `GET` | `/api/v1/me` | |
| `POST` | `/api/v1/sessions` | 409 `SESSION_ALREADY_OPEN` carries the open session's uuid |
| `POST` | `/api/v1/sessions/{uuid}/stop` | Returns session + summary |
| `GET` | `/api/v1/sessions` | Cursor-paginated, newest first |
| `GET` | `/api/v1/sessions/{uuid}` | |
| `GET` | `/api/v1/sessions/{uuid}/inspections` | Cursor-paginated on `fig_seq` |
| `GET` | `/api/v1/sessions/{uuid}/export.csv` | Streaming download |
| `POST` | `/api/v1/sessions/{uuid}/ticket` | 60s ticket for the scanning WebSocket |
| `DELETE` | `/api/v1/sessions/{uuid}` | Cascades to inspections and archived images |
| `GET` | `/api/v1/inspections/{id}/image` | Source frame; 307 to a presigned URL on S3 |
| `GET` | `/api/v1/sessions/{uuid}/report` | Batch report as JSON |
| `GET` | `/api/v1/sessions/{uuid}/report.pdf` | Same report as a PDF download |
| `GET` | `/api/v1/reports/range` | Totals across a date window |

### Reports

`report` and `report.pdf` cover one batch: figs scanned, healthy vs. aflatoxin counts,
contamination rate, throughput, and the model's own statistics — confidence histogram, per-class
means, latency percentiles, and how many figs fell below the 70% review threshold and deserve a
manual look.

**"AI analysis" here means statistics derived from the detector's stored scores, not a language
model.** No external service, no API key, no per-report cost, works offline. The `notes` field
is fixed-threshold observations, not generated prose.

Pass `?fig_weight_g=10` to get an estimated mass. It is a query parameter rather than a stored
column because it varies by variety and drying; omit it and the field is null rather than wrong.

`reports/range` takes ISO-8601 `start`/`end` (defaults: last 30 days, max 366). **Percent-encode
the offset** — a literal `+00:00` in a query string decodes as a space and fails validation; a
`Z` suffix avoids the problem.

### Live scanning

`WS /api/v1/ws/scan/{uuid}?ticket=<ticket>`

A browser cannot set an `Authorization` header on a WebSocket handshake, so the credential
travels in the query string — which proxies log. Hence a short-lived ticket bound to one
session, not the access token.

Client → server: a **binary** message is one JPEG frame; a **text** message is JSON control —
`{"type":"set_conf","value":0.62}`, `{"type":"pause"}`, `{"type":"resume"}`.

Server → client, all JSON: `frame` (detections + stats per processed frame), `inspection` (a fig
was recorded), `stats`, `dropped` (this frame was declined), `error`.

**Every frame draws exactly one reply** — `frame`, `dropped` or `error`. Handle `dropped` as
"that one didn't count, send the next"; it is routine under load, not an error worth showing a
farmer. Ignoring it stalls the client permanently, since the next send waits on a reply that
never comes.

Boxes are `[x1,y1,x2,y2]` normalised 0-1 — scale them by your rendered video size and draw the
overlay client-side. The server does not return annotated images.

**Send the next frame only after the previous reply arrives.** That self-clocks you to
whatever the server can sustain. Frames arriving faster are *dropped, not queued*: the server
keeps only the newest, so a backlog can never build and the boxes never drift behind the belt.
The `dropped` counter in `stats` tells you if you are over-sending. Downscale to 640 px and
encode at JPEG q≈0.7 before sending — roughly 40-60 KB per frame.

Handshake refusals close with `4401` (bad ticket), `4403` (ticket names a different session),
`4404` (session gone), `4409` (already has a live connection), `4410` (session closed).

**Disconnecting does not end the session** — a dropped mobile connection is normal, and
reconnecting resumes the same session with its fig numbering intact. Only `POST /stop` finalises
the totals.

Errors are always `{"error": {"code", "message", "detail?"}}`. Switch on `code` — those strings
are contract; `message` is for humans and may be reworded.

A session belonging to another user returns **404, not 403**. A 403 would confirm the id exists,
which is enough to enumerate other farmers' sessions.

## Database

PostgreSQL is the target; the SQLite default exists so a fresh checkout runs with no
infrastructure. No query is dialect-specific — the desktop DAO's `SUM(decision = 'Aflatoxin')`
was, and PostgreSQL rejects it, so it is now `COUNT(*) FILTER (WHERE ...)`.

The test suite honours `AGROVISION_TEST_DATABASE_URL`, so the same tests run against either
backend. **Run them against PostgreSQL before trusting a persistence change** — that is the
deployment target:

```bash
docker compose up -d

AGROVISION_TEST_DATABASE_URL=postgresql+asyncpg://agrovision:agrovision@127.0.0.1:55432/agrovision_test \
AGROVISION_TEST_S3_ENDPOINT=http://127.0.0.1:59000 \
  .venv/bin/python -m pytest tests -q
```

The S3 tests skip unless `AGROVISION_TEST_S3_ENDPOINT` is set, so the suite still runs with no
object store — but skipped is not passed. Run them before trusting a storage change.

The suite prints the dialect it ran against (`[dialect] postgresql`), so a green run cannot be
mistaken for PostgreSQL coverage it did not have. `tests/infra/test_dialect_behaviour.py` holds
the cases that only mean something there: the native `uuid` type, `TIMESTAMPTZ` timezone
awareness, `ON DELETE CASCADE` enforced by raw SQL rather than by the ORM, and savepoint
isolation — PostgreSQL aborts an entire transaction on a constraint violation unless the failing
statement is wrapped in one.

Schema changes go through Alembic; `tests/infra/test_migrations.py` fails the build if the
migration and the models disagree, and runs against whichever backend is configured. Batch mode
is enabled only for SQLite — on PostgreSQL it would turn ordinary column changes into full table
rebuilds and hold an exclusive lock during deploy.

## Layout

```
app/
├── domain/     framework-free vision logic — no fastapi, sqlalchemy or Qt imports
├── api/v1/     HTTP and WebSocket surface
├── infra/      model provider; persistence and storage land here in Phases 2 and 5
├── core/       logging, security
└── services/   connection and session orchestration (Phase 4)
```

`app/domain` is the ported desktop pipeline and is enforced framework-free by
`tests/domain/test_domain_purity.py`. It is testable without a running server, which the
original — reachable only through a Qt widget tree — was not.

## Behavioural differences from the desktop app

Four fixes are baked into the port; each has a regression test. Detail in the plan, §1.1.

1. **Resolution independence.** The desktop's `min_candidate_area_px = 2500` was calibrated for
   a fixed 1280x720 rig. Browsers send arbitrary resolutions, at which the same fig would be
   silently discarded. Only the area *ratio* filters remain.
2. **Sample-and-time gates.** Frame counts tuned for 30 fps do not survive a 5-10 fps network
   feed. Each gate now needs both a minimum sample count and a minimum duration — see
   `app/domain/gating.py` for why neither floor alone works.
3. **Per-connection state.** Tracking state and the confidence threshold lived on the shared
   engine instance. They are now per-connection; the detector holds only the model.
4. **No silent demo fallback.** A missing model raises rather than quietly returning simulated
   results.

One inherited quirk is deliberately preserved: the `.pt` and `.onnx` paths suppress overlapping
boxes differently (`app/domain/detector.py`, `predict`). Unifying them would change detection
counts, so it waits for the recorded-footage comparison.

## Image storage

Each recorded fig archives the frame that produced it, under
`u{user_id}/{batch_id}/fig_{seq:04d}_{decision}.jpg` — the desktop filename shape
(`utils/path_builder.py:16`) with the date folder replaced by the owning user, since the tenant
is what every listing, deletion and access check is scoped by.

The stored bytes are the client's **original JPEG**, not a re-encode. The desktop app had to
encode because it held a raw camera array; frames now arrive already compressed, so archiving
costs nothing on the hot path and the stored image is byte-identical to what the model
classified — which is what makes it usable as evidence for a disputed reading.

Archiving is best-effort by design. `ImageArchiver` uses a bounded queue and **drops images
rather than blocking**, inherited from `data/image_archiver.py:33`. Storage being slow must cost
archived images, never scanning throughput: a farmer whose belt stalls on an S3 retry has lost
far more than a JPEG.

**Retention is unresolved** (plan §7.2). At ~50 KB per fig and thousands of figs per session,
this is the storage bill. On desktop the images accumulated on the farmer's own disk and were
their problem. `AGROVISION_STORAGE__ENABLED=false` is the blunt answer — decisions and statistics are
kept, no images stored.

## Hardening notes

Rate limits are **per process**. With N replicas the effective ceiling is N×; that is acceptable
while each replica runs a single worker (the model is loaded once per process), and Redis is the
change to make when it stops being true. See `app/core/rate_limit.py`.

Revocation is account-wide, not per-token: `logout-all` bumps `users.token_generation`, which
every replica reads. A `jti` denylist would need shared state, and timestamp cutoffs cannot work
— JWT `iat` has one-second resolution.

**Writes commit inside the handler, never in a dependency teardown.** Since FastAPI 0.106 the
exit half of a `yield` dependency runs after the response is sent, so committing there hands the
client a reply describing a write that is not yet durable. `get_db` deliberately does not commit.

Load harness: `tools/loadtest_scan.py`. The numbers recorded in the plan were measured with the
demo detector and are a floor, not a capacity figure — re-run against real weights before sizing.
