# Figion Backend — Frontend Integration Guide

Everything the web client needs to talk to the backend. The live OpenAPI schema at **`/docs`**
is generated from the code and is the authority if this document ever drifts from it.

Base URL: `/api/v1`. All JSON responses are UTF-8.

---

## 1. Quick orientation

A farmer's session looks like this:

```
register / login          →  access + refresh tokens
POST   /sessions          →  a scanning session (uuid, batch_id)
POST   /sessions/{u}/ticket  →  60-second WebSocket ticket
WS     /ws/scan/{u}?ticket=…  →  stream JPEG frames, receive detections
POST   /sessions/{u}/stop →  finalise totals
GET    /sessions/{u}/report      →  batch report (JSON)
GET    /sessions/{u}/report.pdf  →  same report as a PDF download
```

Two facts that shape the whole client:

- **The camera is yours, not the server's.** You capture with `getUserMedia`, downscale, encode
  JPEG and send frames. The server never sees a camera.
- **The server returns coordinates, not pictures.** Bounding boxes come back normalised 0–1;
  you draw the overlay. No annotated images are sent back — that would roughly double bandwidth
  to redraw something you can already draw from a few numbers.

---

## 2. Authentication

`POST /auth/register` and `POST /auth/login` both return:

```json
{
  "access_token": "eyJ…",
  "refresh_token": "eyJ…",
  "token_type": "bearer",
  "expires_in": 900
}
```

Send the access token on every request:

```
Authorization: Bearer <access_token>
```

| Endpoint | Notes |
|---|---|
| `POST /auth/register` | 201. `409 EMAIL_TAKEN` if the address exists. Password ≥ 8 characters. |
| `POST /auth/login` | `401 INVALID_CREDENTIALS`. Wrong password and unknown email return **identical** responses. |
| `POST /auth/refresh` | Body `{"refresh_token": "…"}`. Only refresh tokens are accepted here. |
| `POST /auth/logout-all` | 204. Invalidates **every** token for the account, including refresh tokens. |
| `GET /me` | Current user. |

### Token handling

Access tokens last **15 minutes**, refresh tokens **30 days**.

On `401`, branch on the error code — they mean different things:

- `TOKEN_EXPIRED` → call `/auth/refresh`, retry the original request.
- `TOKEN_INVALID` / `UNAUTHENTICATED` → send the user back to login. Do **not** retry; this is
  also what you get after `logout-all`, and after an account is deleted.

An access token will not work on `/auth/refresh`, and a refresh token will not work as a bearer
token. This is deliberate.

Credential endpoints are rate-limited per IP (default 10 per minute). Exceeding it returns
`429 RATE_LIMITED` with a `Retry-After` header and `detail.retry_after` in seconds.

---

## 3. Error format

Every error, everywhere, has the same shape:

```json
{
  "error": {
    "code": "SESSION_ALREADY_OPEN",
    "message": "A scanning session is already open",
    "detail": { "session_uuid": "…", "batch_id": "BATCH_20260729_090000" }
  }
}
```

**Switch on `code`.** Those strings are contract and will not change silently. `message` is for
humans and may be reworded; `detail` is optional and code-specific.

| Code | HTTP | Meaning |
|---|---|---|
| `VALIDATION_ERROR` | 422 / 400 / 413 | Bad payload. `detail.fields` lists the offending fields. |
| `UNAUTHENTICATED` | 401 | No token supplied. |
| `TOKEN_EXPIRED` | 401 | Refresh and retry. |
| `TOKEN_INVALID` | 401 | Re-login. Also returned after `logout-all`. |
| `INVALID_CREDENTIALS` | 401 | Wrong email or password. |
| `EMAIL_TAKEN` | 409 | Registration collision. |
| `ACCOUNT_DISABLED` | 403 | Account deactivated. |
| `NOT_FOUND` | 404 | Does not exist **or** is not yours — see below. |
| `SESSION_ALREADY_OPEN` | 409 | `detail.session_uuid` names the open one. |
| `SESSION_CLOSED` | 409 | Session already stopped. |
| `RATE_LIMITED` | 429 | `detail.retry_after` in seconds. |

> **404 vs 403.** Another farmer's session returns **404, not 403**. A 403 would confirm the id
> is real, which is enough to enumerate other users' sessions. Do not present these as "access
> denied" — treat them as "not found".

---

## 4. Sessions

### Start a session

```http
POST /api/v1/sessions
{ "conf_threshold": 0.6, "device_label": "Barn cam" }
```

Both fields optional. Returns `201` with `uuid`, `batch_id`, `ws_url`, and the effective
`conf_threshold`.

**Handle `409 SESSION_ALREADY_OPEN`.** A farmer whose tab closed mid-scan still has an open
session. `detail.session_uuid` tells you which. Offer them a choice: resume that session
(mint a new ticket and reconnect) or stop it and start fresh. Do not silently start a second
one — the first would be stranded with no totals.

### Other session endpoints

| Method | Path | Notes |
|---|---|---|
| `POST` | `/sessions/{uuid}/stop` | Returns `{session, summary}`. Totals recomputed from stored rows. |
| `GET` | `/sessions/{uuid}` | Same `{session, summary}` shape. |
| `GET` | `/sessions` | Cursor-paginated list, newest first. |
| `GET` | `/sessions/{uuid}/inspections` | Cursor-paginated fig list, newest fig first. |
| `DELETE` | `/sessions/{uuid}` | 204. Cascades to inspections **and archived images**. Irreversible. |
| `GET` | `/sessions/{uuid}/export.csv` | Streaming CSV download. |

### Pagination

Cursor-based, not offset-based — rows are inserted constantly while a farmer scrolls, and an
offset would shift items between pages.

```
GET /sessions?limit=25
→ { "items": [...], "next_cursor": 412 }

GET /sessions?limit=25&cursor=412
→ { "items": [...], "next_cursor": null }     ← null means last page
```

`limit` is capped at 100; asking for more returns `422`.

---

## 5. Live scanning (WebSocket)

### Getting connected

A browser **cannot** set an `Authorization` header on a WebSocket handshake, so the credential
travels in the query string — where proxies log it. That is why you exchange your access token
for a short ticket first:

```http
POST /api/v1/sessions/{uuid}/ticket
→ { "ticket": "eyJ…", "ws_url": "/api/v1/ws/scan/{uuid}", "expires_in": 60 }
```

Then connect within 60 seconds:

```js
const ws = new WebSocket(`wss://host/api/v1/ws/scan/${uuid}?ticket=${ticket}`);
```

The ticket is bound to that one session and cannot be replayed against another. Mint a fresh
one for every connection attempt — including reconnects.

**Handshake close codes:**

| Code | Meaning |
|---|---|
| `4401` | Ticket invalid or expired → mint a new one |
| `4403` | Ticket is for a different session |
| `4404` | Session not found |
| `4409` | This session already has a live connection |
| `4410` | Session is closed |
| `4429` | Too many concurrent connections for this user (default 3) |

### Sending frames

**Binary** messages are JPEG frames. **Text** messages are JSON control:

```json
{"type": "set_conf", "value": 0.62}
{"type": "pause"}
{"type": "resume"}
```

Capture guidance — bandwidth is the binding constraint on a rural connection:

- Downscale to **640 px** on the long edge.
- Encode JPEG at **quality ≈ 0.7** → roughly 40–60 KB per frame.
- Target **5–10 fps**. Sending raw 720p at 30 fps would be ~4 MB/s per farmer and will not work.

Frames larger than 2 MB, or declaring dimensions above 1920×1080, are rejected.

### Receiving

```json
// one processed frame
{"type":"frame","latency_ms":88.4,
 "detections":[{"class_name":"Aflatoxin","confidence":0.91,"bbox":[0.21,0.30,0.55,0.71]}],
 "stats":{"active_slots":2,"locked_slots":1,"effective_fps":7.3,
          "received":412,"processed":98,"dropped":314,"rejected":0,"recorded":37}}

// a fig was confirmed and recorded — this is the one that counts
{"type":"inspection","fig_seq":37,"decision":"Healthy","confidence":0.88,
 "latency_ms":91.0,"timestamp":"2026-07-29T10:12:03Z",
 "image_url":"/api/v1/inspections/9912/image"}

// running totals, so you never recompute them
{"type":"stats","received":412,"processed":98,"dropped":314,"rejected":0,"recorded":37}

// this frame was declined — see below, this matters
{"type":"dropped","reason":"rate"}

{"type":"error","code":"FRAME_TOO_LARGE","message":"…"}
```

### ⚠ The one thing you must get right

**Send the next frame only after the previous one is answered, and treat `dropped` as an
answer.**

```js
let inFlight = false;

async function pump() {
  if (inFlight) return;
  inFlight = true;
  ws.send(await captureJpegBlob());
}

ws.onmessage = (event) => {
  const msg = JSON.parse(event.data);
  if (msg.type === "frame")      { draw(msg.detections); inFlight = false; pump(); }
  if (msg.type === "dropped")    { inFlight = false; pump(); }   // ← do not skip this
  if (msg.type === "error")      { inFlight = false; pump(); }
  if (msg.type === "inspection") { appendToLog(msg); }           // does not clear inFlight
};
```

Every frame draws **exactly one** of `frame`, `dropped` or `error`. If you ignore `dropped`,
your client will stall permanently the first time the server declines a frame — which happens
routinely under load. `dropped` is normal, not an error condition; do not surface it to the
farmer.

`reason` is `"rate"` (you are sending faster than the server accepts), `"superseded"` (a newer
frame replaced this one) or `"paused"`.

`image_url` on an `inspection` is **`null` when image archiving is disabled** or the archiver's
queue was full — the fig is still recorded, only its picture is not. Guard the thumbnail render
on it rather than assuming a URL is always present. It also needs the `Authorization` header,
so fetch it as a blob rather than putting it in an `<img src>`.

Frames are **dropped, never queued**. The server keeps only the newest, so a backlog can never
build and the boxes never drift behind the belt. A rising `dropped` counter means you are
over-sending — back off your capture rate.

### Drawing boxes

`bbox` is `[x1, y1, x2, y2]` normalised 0–1 against the source frame. Multiply by your rendered
video element's dimensions:

```js
const x = bbox[0] * videoEl.clientWidth;
const y = bbox[1] * videoEl.clientHeight;
const w = (bbox[2] - bbox[0]) * videoEl.clientWidth;
const h = (bbox[3] - bbox[1]) * videoEl.clientHeight;
```

`class_name` is `"Aflatoxin"` or `"Healthy"`.

### Disconnection

**Disconnecting does not end the session.** A dropped mobile connection is normal; reconnect
with a fresh ticket and the same session resumes with its fig numbering intact. Only
`POST /stop` finalises totals.

---

## 6. Reports

### `GET /sessions/{uuid}/report`

Optional `?fig_weight_g=9.5` adds an estimated mass. Omit it and `estimated_mass_g` is `null`
rather than a wrong number — fig weight varies by variety and drying, so the client supplies it.

```json
{
  "batch_id": "BATCH_20260729_090000",
  "device_label": "Barn cam",
  "started_at": "2026-07-29T09:00:00Z",
  "ended_at": "2026-07-29T09:07:00Z",
  "is_open": false,
  "throughput": {
    "total_figs": 100,
    "healthy_count": 86,
    "aflatoxin_count": 14,
    "defect_rate_pct": 14.0,
    "duration_seconds": 420.0,
    "figs_per_minute": 14.29,
    "estimated_mass_g": 950.0
  },
  "analysis": {
    "mean_confidence": 0.906,
    "median_confidence": 0.9,
    "low_confidence_count": 8,
    "low_confidence_pct": 8.0,
    "low_confidence_threshold": 0.7,
    "confidence_histogram": [
      {"label": "0%-50%",   "lower": 0.0, "upper": 0.5, "count": 0},
      {"label": "50%-60%",  "lower": 0.5, "upper": 0.6, "count": 0},
      {"label": "60%-70%",  "lower": 0.6, "upper": 0.7, "count": 8},
      {"label": "70%-80%",  "lower": 0.7, "upper": 0.8, "count": 0},
      {"label": "80%-90%",  "lower": 0.8, "upper": 0.9, "count": 14},
      {"label": "90%-100%", "lower": 0.9, "upper": 1.0, "count": 78}
    ],
    "per_class": [
      {"decision": "Aflatoxin", "count": 14, "share_pct": 14.0,
       "mean_confidence": 0.88,   "min_confidence": 0.88},
      {"decision": "Healthy",   "count": 86, "share_pct": 86.0,
       "mean_confidence": 0.9102, "min_confidence": 0.62}
    ],
    "latency_p50_ms": 82.0,
    "latency_p95_ms": 120.0,
    "latency_max_ms": 120.0,
    "conf_threshold_used": 0.6
  },
  "notes": ["8 fig(s) scored below 70% confidence and are worth a manual look."]
}
```

**Rendering hints:**

- `throughput` is the headline: figs scanned, healthy, aflatoxin, contamination rate.
- `analysis` is the model reporting on its own output — a histogram bar chart maps directly onto
  `confidence_histogram`. `per_class` always contains **both** decisions, with zero counts when a
  class is absent, so a chart never loses an axis.
- `low_confidence_count` is a **worklist**, not an error rate: figs a human should recheck.
- `conf_threshold_used` must be displayed. Figs scored under a different threshold are not
  comparable, so a report without it is ambiguous.
- `notes` are ready-to-display sentences. Render them as a bulleted list; do not parse them.
- `is_open: true` means the session is still running and the numbers will keep moving.

### `GET /sessions/{uuid}/report.pdf`

Same data, same query parameters, as a PDF download. `Content-Disposition` names the file
`{batch_id}_report.pdf`. Trigger it as a normal download — but remember it needs the
`Authorization` header, so a plain `<a href>` will not work. Fetch it as a blob:

```js
const res = await fetch(url, { headers: { Authorization: `Bearer ${token}` } });
const blob = await res.blob();
// then createObjectURL + a synthetic <a download> click
```

### `GET /reports/range`

Totals across a date window. Defaults to the last 30 days; maximum span 366 days.

```
GET /api/v1/reports/range?start=2026-07-01T00:00:00Z&end=2026-07-31T00:00:00Z
→ { "start": …, "end": …, "sessions": 12, "total_figs": 4210,
    "healthy_count": 3980, "aflatoxin_count": 230,
    "defect_rate_pct": 5.46, "mean_confidence": 0.91 }
```

> **Percent-encode timestamps.** A literal `+00:00` in a query string decodes as a space and
> fails validation. Use the `Z` suffix, or `encodeURIComponent()`.

---

## 7. Images

`GET /inspections/{id}/image` returns the source frame for a recorded fig. With S3 storage
configured it responds `307` to a short-lived presigned URL; with local storage it streams the
bytes. Either way it is behind the same ownership check as everything else — another farmer's
image is a 404.

The inspection `id` comes from `GET /sessions/{uuid}/inspections`.

---

## 8. Health and capability

| Endpoint | Use |
|---|---|
| `GET /health` | Liveness. Never touches the database. |
| `GET /health/ready` | Readiness. `503` when a dependency is down. |
| `GET /model/info` | `{backend, demo_mode, class_names, input_size}` |

**Check `demo_mode` on startup.** When true the server has no real weights loaded and is
returning **simulated** results. The UI must say so unmistakably — a farmer must never mistake
demo output for a real aflatoxin reading.

---

## 9. Things that will bite you

1. **Ignoring `dropped`** freezes the scan loop permanently. See §5.
2. **A plain `<a href>` for CSV/PDF** loses the `Authorization` header. Fetch as a blob.
3. **`+00:00` in query strings** decodes as a space. Use `Z` or encode it.
4. **404 does not mean "gone"** — it also means "not yours". Do not offer a "request access" flow.
5. **`409 SESSION_ALREADY_OPEN`** is expected after a tab closes mid-scan, not an edge case.
   Build the resume/stop choice.
6. **`demo_mode: true`** means the numbers are fabricated.
7. **UV illumination is required.** The model was trained on UV fluorescence imagery. A farmer
   pointing an ordinary webcam at figs in daylight gets confident-looking, meaningless output.
   The product should state this before the first scan.
