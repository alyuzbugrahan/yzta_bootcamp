"""The Phase 3 acceptance scenario: register → start → record → stop → summary → CSV.

Runs end to end over HTTP with no WebSocket involved. Inspections are written directly through
the repository, standing in for what the realtime pipeline will do in Phase 4.
"""

from __future__ import annotations

import csv
import io
import uuid as uuid_module

from app.domain.models import Detection, InspectionResult
from app.infra.repositories.inspection_repository import InspectionRepository
from app.infra.repositories.session_repository import SessionRepository
from app.services.csv_export import BOM, CSV_HEADERS


def inspection(decision: str, confidence: float = 0.9) -> InspectionResult:
    return InspectionResult(
        decision=decision,
        confidence=confidence,
        detection=Detection(decision, confidence, (0.2, 0.2, 0.6, 0.6)),
        latency_ms=88.0,
    )


async def record_figs(app, user_id: int, session_uuid: uuid_module.UUID, decisions: list[str]):
    """Write inspections the way the scan pipeline will."""
    factory = app.state.session_factory
    async with factory() as db:
        scan = await SessionRepository(db).get(user_id, session_uuid)
        repository = InspectionRepository(db)
        for decision in decisions:
            await repository.record(scan.id, inspection(decision))
        await db.commit()


async def test_full_harvest_lifecycle(client, app):
    # ── Register ──────────────────────────────────────────────────────────
    registration = await client.post(
        "/api/v1/auth/register",
        json={"email": "harvest@example.com", "password": "harvest-2026"},
    )
    assert registration.status_code == 201
    headers = {"Authorization": f"Bearer {registration.json()['access_token']}"}

    identity = await client.get("/api/v1/me", headers=headers)
    user_id = identity.json()["id"]

    # ── Start scanning ────────────────────────────────────────────────────
    started = await client.post(
        "/api/v1/sessions",
        json={"conf_threshold": 0.6, "device_label": "Greenhouse UV rig"},
        headers=headers,
    )
    assert started.status_code == 201
    session_uuid = started.json()["uuid"]
    batch_id = started.json()["batch_id"]

    # ── Figs go past the camera ───────────────────────────────────────────
    await record_figs(
        app,
        user_id,
        uuid_module.UUID(session_uuid),
        ["Healthy", "Aflatoxin", "Healthy", "Healthy", "Aflatoxin"],
    )

    # ── Live listing ──────────────────────────────────────────────────────
    listing = await client.get(
        f"/api/v1/sessions/{session_uuid}/inspections", headers=headers
    )
    assert listing.status_code == 200
    assert [row["fig_seq"] for row in listing.json()["items"]] == [5, 4, 3, 2, 1]

    # ── Stop ──────────────────────────────────────────────────────────────
    stopped = await client.post(f"/api/v1/sessions/{session_uuid}/stop", headers=headers)
    assert stopped.status_code == 200

    session_body = stopped.json()["session"]
    summary = stopped.json()["summary"]

    assert session_body["is_open"] is False
    assert session_body["total_count"] == 5
    assert session_body["defect_count"] == 2
    assert summary["total"] == 5
    assert summary["aflatoxin"] == 2
    assert summary["healthy"] == 3
    assert summary["ratio_pct"] == 40.0

    # ── History ───────────────────────────────────────────────────────────
    history = await client.get("/api/v1/sessions", headers=headers)
    assert [row["uuid"] for row in history.json()["items"]] == [session_uuid]

    # ── Export ────────────────────────────────────────────────────────────
    export = await client.get(f"/api/v1/sessions/{session_uuid}/export.csv", headers=headers)
    assert export.status_code == 200
    assert export.headers["content-type"].startswith("text/csv")
    assert f'filename="{batch_id}.csv"' in export.headers["content-disposition"]

    text = export.text
    assert text.startswith(BOM)

    rows = list(csv.reader(io.StringIO(text.lstrip(BOM))))
    assert rows[0] == CSV_HEADERS
    assert len(rows) == 6
    assert [row[0] for row in rows[1:]] == ["1", "2", "3", "4", "5"]
    assert [row[3] for row in rows[1:]] == [
        "Healthy",
        "Aflatoxin",
        "Healthy",
        "Healthy",
        "Aflatoxin",
    ]
    assert all(row[1] == batch_id for row in rows[1:])


async def test_export_streams_without_the_request_session(client, app, farmer):
    """Guards the bug this endpoint is written around.

    A ``yield`` dependency is torn down before a streaming body finishes, so an export using
    the request-scoped session would fail once the response started — and only for sessions
    with rows, which a smoke test on an empty session would miss.
    """
    started = await client.post("/api/v1/sessions", json={}, headers=farmer.headers)
    session_uuid = started.json()["uuid"]

    identity = await client.get("/api/v1/me", headers=farmer.headers)
    await record_figs(
        app, identity.json()["id"], uuid_module.UUID(session_uuid), ["Healthy"] * 20
    )

    export = await client.get(
        f"/api/v1/sessions/{session_uuid}/export.csv", headers=farmer.headers
    )

    rows = list(csv.reader(io.StringIO(export.text.lstrip(BOM))))

    assert export.status_code == 200
    assert len(rows) == 21


async def test_export_of_an_unknown_session_is_json_not_an_empty_file(client, farmer):
    response = await client.get(
        f"/api/v1/sessions/{uuid_module.uuid4()}/export.csv", headers=farmer.headers
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
