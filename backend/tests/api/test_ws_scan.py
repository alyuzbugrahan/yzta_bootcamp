"""Realtime scanning over a real WebSocket.

Synchronous, using Starlette's ``TestClient``: it drives a genuine handshake and runs the app's
lifespan on its own event loop, which an async httpx client cannot do for WebSockets.

The plan's acceptance criterion asks for a *recorded* frame sequence compared against desktop
output. No UV footage ships with the repository, so the sequences here are synthesised and the
assertions pin the slot semantics the desktop pipeline had — one physical fig produces exactly
one record, and a brief dropout does not produce a second. Those are the same properties the
Phase 1 domain tests fix, now exercised end to end through the socket, the thread pool and the
database. Comparing against real footage remains open; see docs/WEB_MIGRATION_PLAN.md §5.
"""

from __future__ import annotations

import asyncio
import os
import time
import uuid as uuid_module

import cv2
import numpy as np
import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.api.v1.ws_scan import (
    WS_CONFLICT,
    WS_FORBIDDEN,
    WS_NOT_FOUND,
    WS_SESSION_CLOSED,
    WS_UNAUTHORIZED,
)
from app.config import Settings
from app.domain.models import Detection
from app.infra.db.models import Base
from app.infra.db.session import create_engine
from app.main import create_app

# ── Fixtures ──────────────────────────────────────────────────────────────


def _database_url(tmp_path) -> str:
    return os.getenv("AGROVISION_TEST_DATABASE_URL") or f"sqlite+aiosqlite:///{tmp_path}/ws.db"


async def _reset(url: str) -> None:
    engine = create_engine(url)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.drop_all)
        await connection.run_sync(Base.metadata.create_all)
    await engine.dispose()


class ScriptedDetector:
    """Returns the same detections every frame, so slot behaviour is deterministic."""

    def __init__(self, detections: list[Detection] | None = None, delay: float = 0.0) -> None:
        self._detections = detections if detections is not None else [_fig()]
        self._delay = delay
        self.calls = 0
        self.seen_conf: list[float] = []

    @property
    def backend(self) -> str:
        return "scripted"

    @property
    def is_demo(self) -> bool:
        return False

    def predict(self, frame, conf, iou):
        self.calls += 1
        self.seen_conf.append(conf)
        if self._delay:
            time.sleep(self._delay)
        return list(self._detections)


def _fig(class_name: str = "Healthy", bbox=(0.2, 0.2, 0.6, 0.6)) -> Detection:
    return Detection(class_name=class_name, confidence=0.91, bbox=bbox)


def jpeg(width: int = 640, height: int = 480) -> bytes:
    image = np.zeros((height, width, 3), dtype=np.uint8)
    cv2.ellipse(
        image, (width // 2, height // 2), (width // 8, height // 8), 0, 0, 360, (220,) * 3, -1
    )
    ok, buffer = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, 80])
    assert ok
    return buffer.tobytes()


@pytest.fixture
def make_client(tmp_path):
    """Builds a TestClient with a swappable detector and overridable settings."""
    created: list[TestClient] = []

    def _build(detector=None, **overrides):
        url = _database_url(tmp_path)
        asyncio.run(_reset(url))

        settings = Settings(
            environment="dev",
            log_json=False,
            database={"url": url},
            model={"allow_demo": True},
            **overrides,
        )
        app = create_app(settings)
        client = TestClient(app)
        client.__enter__()
        created.append(client)

        # Replace the shared detector after lifespan has run.
        app.state.detector = detector or ScriptedDetector()
        return client

    yield _build

    for client in created:
        client.__exit__(None, None, None)


def register(client: TestClient, email: str = "farmer@example.com") -> dict:
    response = client.post(
        "/api/v1/auth/register", json={"email": email, "password": "harvest-2026"}
    )
    assert response.status_code == 201, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def open_session(client: TestClient, headers: dict) -> str:
    response = client.post("/api/v1/sessions", json={}, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["uuid"]


def ticket_for(client: TestClient, headers: dict, session_uuid: str) -> str:
    response = client.post(f"/api/v1/sessions/{session_uuid}/ticket", headers=headers)
    assert response.status_code == 200, response.text
    return response.json()["ticket"]


def ws_path(session_uuid: str, ticket: str) -> str:
    return f"/api/v1/ws/scan/{session_uuid}?ticket={ticket}"


def assert_handshake_rejected(client: TestClient, path: str, code: int) -> None:
    """Assert the server refuses the handshake with a specific close code.

    Asserting the code rather than merely "an exception was raised" is what distinguishes a
    deliberate refusal from an unrelated crash during connect — the two look identical
    otherwise.
    """
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect(path) as ws:
        ws.receive_json()

    assert exc.value.code == code, f"expected close {code}, got {exc.value.code}"


def receive_until(ws, wanted: set[str], limit: int = 200) -> dict:
    """Read past messages that are not of interest.

    Needed because the server now acknowledges frames it declines to process
    (``{"type": "dropped"}``), so a ``frame`` reply is no longer necessarily the next message.
    Before that acknowledgement existed a declined frame produced no reply at all, and a
    self-clocking client waited for one forever.
    """
    for _ in range(limit):
        message = ws.receive_json()
        if message["type"] in wanted:
            return message
    raise AssertionError(f"no message of type {wanted} within {limit} messages")


def drain(ws, count: int) -> list[dict]:
    """Collect exactly ``count`` messages.

    ``receive_json`` blocks with no timeout, so callers must know how many messages the server
    will actually send. Asking for more than that hangs until the suite-level timeout — every
    frame now draws exactly one reply, whether processed or declined.
    """
    return [ws.receive_json() for _ in range(count)]


# ── Handshake and authorization ───────────────────────────────────────────


def test_connects_with_a_valid_ticket(make_client):
    client = make_client()
    headers = register(client)
    session_uuid = open_session(client, headers)
    ticket = ticket_for(client, headers, session_uuid)

    with client.websocket_connect(ws_path(session_uuid, ticket)) as ws:
        ws.send_bytes(jpeg())
        message = ws.receive_json()

    assert message["type"] == "frame"


def test_invalid_ticket_is_refused(make_client):
    client = make_client()
    headers = register(client)
    session_uuid = open_session(client, headers)

    assert_handshake_rejected(client, ws_path(session_uuid, "not-a-ticket"), WS_UNAUTHORIZED)


def test_access_token_is_not_accepted_as_a_ticket(make_client):
    """Tickets are a distinct token type; a general access token must not open a socket."""
    client = make_client()
    headers = register(client)
    session_uuid = open_session(client, headers)
    access_token = headers["Authorization"].removeprefix("Bearer ")

    assert_handshake_rejected(client, ws_path(session_uuid, access_token), WS_UNAUTHORIZED)


def test_ticket_cannot_be_replayed_against_another_session(make_client):
    """The ``sid`` claim binds a ticket to one session.

    Without it, a ticket minted for a session the farmer owns would open a socket onto any
    session uuid they cared to type — including another farmer's.
    """
    client = make_client()
    headers = register(client)
    first = open_session(client, headers)
    ticket = ticket_for(client, headers, first)

    client.post(f"/api/v1/sessions/{first}/stop", headers=headers)
    second = open_session(client, headers)

    assert_handshake_rejected(client, ws_path(second, ticket), WS_FORBIDDEN)


def test_another_farmers_session_cannot_be_opened(make_client):
    client = make_client()
    owner = register(client, "owner@example.com")
    intruder = register(client, "intruder@example.com")
    session_uuid = open_session(client, owner)

    # A ticket for a session the intruder does not own cannot even be minted.
    denied = client.post(f"/api/v1/sessions/{session_uuid}/ticket", headers=intruder)

    assert denied.status_code == 404


def test_ticket_pointed_at_an_unknown_session_is_refused(make_client):
    """The ``sid`` check fires before the database is consulted.

    A ticket naming session A presented at the URL for some other uuid is a mismatch, so the
    refusal is 4403 rather than 4404 — the server never needs to look up whether that uuid
    exists, which is also what stops the endpoint being used to probe for real session ids.
    """
    client = make_client()
    headers = register(client)
    session_uuid = open_session(client, headers)
    ticket = ticket_for(client, headers, session_uuid)

    assert_handshake_rejected(
        client, ws_path(str(uuid_module.uuid4()), ticket), WS_FORBIDDEN
    )


def test_ticket_for_a_deleted_session_is_refused(make_client):
    """The one path that genuinely reaches 4404.

    The ticket and the URL agree, but the row is gone — deleted in the window between minting
    the ticket and connecting.
    """
    client = make_client()
    headers = register(client)
    session_uuid = open_session(client, headers)
    ticket = ticket_for(client, headers, session_uuid)

    assert client.delete(f"/api/v1/sessions/{session_uuid}", headers=headers).status_code == 204

    assert_handshake_rejected(client, ws_path(session_uuid, ticket), WS_NOT_FOUND)


def test_closed_session_cannot_be_scanned(make_client):
    client = make_client()
    headers = register(client)
    session_uuid = open_session(client, headers)
    ticket = ticket_for(client, headers, session_uuid)
    client.post(f"/api/v1/sessions/{session_uuid}/stop", headers=headers)

    assert_handshake_rejected(client, ws_path(session_uuid, ticket), WS_SESSION_CLOSED)


def test_second_connection_to_the_same_session_is_refused(make_client):
    """Two live connections would each keep their own slot state while sharing one fig_seq
    series — every fig recorded twice."""
    client = make_client()
    headers = register(client)
    session_uuid = open_session(client, headers)

    first_ticket = ticket_for(client, headers, session_uuid)
    second_ticket = ticket_for(client, headers, session_uuid)

    with client.websocket_connect(ws_path(session_uuid, first_ticket)) as ws:
        ws.send_bytes(jpeg())
        ws.receive_json()

        assert_handshake_rejected(
            client, ws_path(session_uuid, second_ticket), WS_CONFLICT
        )


def test_connection_slot_is_released_on_disconnect(make_client):
    client = make_client()
    headers = register(client)
    session_uuid = open_session(client, headers)

    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        ws.send_bytes(jpeg())
        ws.receive_json()

    # Reconnecting after a clean close must work.
    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        ws.send_bytes(jpeg())
        assert ws.receive_json()["type"] == "frame"


# ── Detection flow ────────────────────────────────────────────────────────


def test_frame_message_carries_normalised_boxes(make_client):
    """The browser draws the overlay, so boxes travel as numbers, not pixels.

    Two frames, spaced past the confirm gate: the stabiliser withholds a detection until it has
    been seen twice and for at least 70 ms, so the first frame legitimately reports nothing.
    """
    client = make_client(
        ScriptedDetector([_fig("Aflatoxin", (0.1, 0.2, 0.3, 0.4))]), ingest={"max_fps": 1000}
    )
    headers = register(client)
    session_uuid = open_session(client, headers)

    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        ws.send_bytes(jpeg())
        time.sleep(0.1)
        ws.send_bytes(jpeg())
        messages = drain(ws, 2)

    boxes = [d for m in messages if m["type"] == "frame" for d in m["detections"]]

    assert boxes, "no detections were reported"
    assert boxes[0]["class_name"] == "Aflatoxin"
    assert boxes[0]["bbox"] == [0.1, 0.2, 0.3, 0.4]
    assert all(0.0 <= v <= 1.0 for v in boxes[0]["bbox"])


def test_one_fig_produces_exactly_one_inspection(make_client):
    """The core slot property, end to end.

    A fig sitting under the camera for many frames is recorded once — the desktop behaviour
    pinned by ``test_stationary_fig_is_recorded_exactly_once`` in the domain suite, now
    travelling through the socket, the thread pool and the database.
    """
    client = make_client(ScriptedDetector(), ingest={"max_fps": 1000})
    headers = register(client)
    session_uuid = open_session(client, headers)

    inspections = []
    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        for _ in range(12):
            ws.send_bytes(jpeg())
            time.sleep(0.04)
            while True:
                message = ws.receive_json()
                if message["type"] == "inspection":
                    inspections.append(message)
                if message["type"] in {"frame", "stats"}:
                    break

    assert len(inspections) == 1, f"fig recorded {len(inspections)} times"
    assert inspections[0]["fig_seq"] == 1
    assert inspections[0]["decision"] == "Healthy"


def test_inspections_are_persisted_and_visible_over_rest(make_client):
    client = make_client(ScriptedDetector(), ingest={"max_fps": 1000})
    headers = register(client)
    session_uuid = open_session(client, headers)

    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        for _ in range(12):
            ws.send_bytes(jpeg())
            time.sleep(0.04)
            ws.receive_json()

    listing = client.get(f"/api/v1/sessions/{session_uuid}/inspections", headers=headers)
    rows = listing.json()["items"]

    assert len(rows) == 1
    assert rows[0]["decision"] == "Healthy"
    assert rows[0]["fig_seq"] == 1


def test_session_stays_open_after_disconnect(make_client):
    """A dropped mobile connection is normal and must not finalise the harvest.

    Totals are written by an explicit POST /stop, so reconnecting resumes the same session with
    its fig_seq series intact.
    """
    client = make_client()
    headers = register(client)
    session_uuid = open_session(client, headers)

    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        ws.send_bytes(jpeg())
        ws.receive_json()

    detail = client.get(f"/api/v1/sessions/{session_uuid}", headers=headers)

    assert detail.json()["session"]["is_open"] is True


# ── Backpressure ──────────────────────────────────────────────────────────


def test_frames_are_dropped_rather_than_queued(make_client):
    """The central backpressure property.

    Inference here takes 80 ms; frames are pushed far faster. A queue would grow without bound
    and the boxes on screen would fall further behind the belt every second. The single-slot
    mailbox keeps only the newest frame, so the client sees fewer updates but always recent
    ones.
    """
    client = make_client(ScriptedDetector(delay=0.08), ingest={"max_fps": 1000})
    headers = register(client)
    session_uuid = open_session(client, headers)

    sent = 40
    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        for _ in range(sent):
            ws.send_bytes(jpeg())
        time.sleep(1.0)
        ws.send_bytes(jpeg())
        message = receive_until(ws, {"frame"})

    stats = message["stats"]

    assert stats["received"] >= sent
    assert stats["dropped"] > 0, "frames were queued instead of dropped"
    assert stats["processed"] < stats["received"], "every frame was processed — no drop occurred"


def test_arrival_rate_is_capped(make_client):
    """A flood is discarded before validation, so it costs almost nothing to absorb.

    At 2 fps the gate admits one frame per 500 ms. Thirty frames sent back to back therefore
    yield exactly one processed frame and twenty-nine refusals.

    Every frame draws exactly one reply, so the replies are counted rather than sampled.
    Inspecting the counters on a single message would race: the first frame is answered before
    the rest of the burst has even been read off the socket.
    """
    client = make_client(ScriptedDetector(), ingest={"max_fps": 2})
    headers = register(client)
    session_uuid = open_session(client, headers)

    burst = 30

    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        for _ in range(burst):
            ws.send_bytes(jpeg())
        replies = drain(ws, burst)

        # Past the 500 ms window, so this one is admitted.
        time.sleep(0.6)
        ws.send_bytes(jpeg())
        replies.append(receive_until(ws, {"frame", "dropped"}))

    declined = [m for m in replies if m["type"] == "dropped"]
    processed = [m for m in replies if m["type"] == "frame"]

    assert len(declined) > 20, f"gate admitted {len(processed)} of {burst + 1}"
    assert all(m["reason"] == "rate" for m in declined)
    assert processed[-1]["stats"]["processed"] == 2


def test_latency_stays_bounded_under_load(make_client):
    """Reported latency is per-frame processing time, not time-since-capture.

    Because stale frames are discarded rather than queued, this figure cannot drift upward the
    way a backlog's would.
    """
    client = make_client(ScriptedDetector(delay=0.05), ingest={"max_fps": 1000})
    headers = register(client)
    session_uuid = open_session(client, headers)

    latencies = []
    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        for _ in range(20):
            ws.send_bytes(jpeg())
            time.sleep(0.01)
        for _ in range(3):
            ws.send_bytes(jpeg())
            time.sleep(0.15)
            latencies.append(receive_until(ws, {"frame"})["latency_ms"])

    assert latencies, "no frame messages received"
    assert max(latencies) < 1000, f"latency grew unbounded: {latencies}"


# ── Control channel ───────────────────────────────────────────────────────


def test_set_conf_changes_the_threshold_for_this_connection_only(make_client):
    """The desktop slider mutated shared engine state; here it is per-connection."""
    detector = ScriptedDetector()
    client = make_client(detector, ingest={"max_fps": 1000})
    headers = register(client)
    session_uuid = open_session(client, headers)

    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        ws.send_bytes(jpeg())
        ws.receive_json()

        ws.send_json({"type": "set_conf", "value": 0.85})
        time.sleep(0.05)
        ws.send_bytes(jpeg())
        ws.receive_json()

    assert detector.seen_conf[0] != pytest.approx(0.85)
    assert detector.seen_conf[-1] == pytest.approx(0.85)


def test_pause_stops_processing_and_resume_restarts_it(make_client):
    client = make_client(ScriptedDetector(), ingest={"max_fps": 1000})
    headers = register(client)
    session_uuid = open_session(client, headers)

    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        ws.send_bytes(jpeg())
        processed_before = receive_until(ws, {"frame"})["stats"]["processed"]

        ws.send_json({"type": "pause"})
        time.sleep(0.05)
        for _ in range(5):
            ws.send_bytes(jpeg())
        time.sleep(0.2)

        ws.send_json({"type": "resume"})
        time.sleep(0.05)
        ws.send_bytes(jpeg())
        after = receive_until(ws, {"frame"})

    assert after["stats"]["processed"] == processed_before + 1


def test_invalid_control_message_is_reported(make_client):
    client = make_client()
    headers = register(client)
    session_uuid = open_session(client, headers)

    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        ws.send_text("this is not json")
        message = ws.receive_json()

    assert message["type"] == "error"
    assert message["code"] == "BAD_CONTROL"


def test_out_of_range_confidence_is_rejected(make_client):
    client = make_client()
    headers = register(client)
    session_uuid = open_session(client, headers)

    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        ws.send_json({"type": "set_conf", "value": 5.0})
        message = ws.receive_json()

    assert message["type"] == "error"
    assert message["code"] == "BAD_CONTROL"


# ── Malformed frames ──────────────────────────────────────────────────────


def test_non_jpeg_payload_is_reported_without_closing(make_client):
    """One bad frame costs one frame, not the farmer's session."""
    client = make_client(ScriptedDetector(), ingest={"max_fps": 1000})
    headers = register(client)
    session_uuid = open_session(client, headers)

    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        ws.send_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 128)
        error = ws.receive_json()

        time.sleep(0.05)
        ws.send_bytes(jpeg())
        recovered = ws.receive_json()

    assert error["type"] == "error"
    assert error["code"] == "FRAME_NOT_JPEG"
    assert recovered["type"] == "frame", "connection did not survive a bad frame"


def test_oversized_frame_is_rejected(make_client):
    client = make_client(ScriptedDetector(), ingest={"max_fps": 1000, "max_frame_bytes": 500})
    headers = register(client)
    session_uuid = open_session(client, headers)

    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        ws.send_bytes(jpeg(1280, 720))
        message = ws.receive_json()

    assert message["type"] == "error"
    assert message["code"] == "FRAME_TOO_LARGE"


def test_a_declined_frame_is_still_acknowledged(make_client):
    """Every frame gets a reply, even one the server refuses to process.

    §4.2 tells clients to send the next frame only once the previous one is answered. A
    silently dropped frame therefore deadlocks a well-behaved client permanently — the load
    harness hit exactly this: eight frames, then a stall until timeout. The rate gate is the
    easiest way to provoke a drop.
    """
    client = make_client(ScriptedDetector(), ingest={"max_fps": 1})
    headers = register(client)
    session_uuid = open_session(client, headers)

    with client.websocket_connect(
        ws_path(session_uuid, ticket_for(client, headers, session_uuid))
    ) as ws:
        ws.send_bytes(jpeg())
        receive_until(ws, {"frame"})

        # Immediately again — far inside the 1 fps gate, so this one is declined.
        ws.send_bytes(jpeg())
        reply = receive_until(ws, {"frame", "dropped"})

    assert reply["type"] == "dropped", "declined frame produced no reply at all"
    assert reply["reason"] == "rate"
