"""Batch report endpoints: JSON, PDF and the date-range aggregate."""

from __future__ import annotations

import uuid as uuid_module
from datetime import UTC, datetime, timedelta
from urllib.parse import quote

from app.domain.models import Detection, InspectionResult
from app.infra.repositories.inspection_repository import InspectionRepository
from app.infra.repositories.session_repository import SessionRepository


def inspection(decision: str, confidence: float = 0.9, latency: float = 80.0):
    return InspectionResult(
        decision=decision,
        confidence=confidence,
        detection=Detection(decision, confidence, (0.2, 0.2, 0.6, 0.6)),
        latency_ms=latency,
    )


async def record(app, user_id: int, session_uuid: str, rows: list[tuple[str, float]]):
    """Write inspections directly, standing in for the scanning pipeline."""
    factory = app.state.session_factory
    async with factory() as db:
        scan = await SessionRepository(db).get(user_id, uuid_module.UUID(session_uuid))
        repository = InspectionRepository(db)
        for decision, confidence in rows:
            await repository.record(scan.id, inspection(decision, confidence))
        await db.commit()


async def user_id_of(client, account) -> int:
    response = await client.get("/api/v1/me", headers=account.headers)
    return response.json()["id"]


async def scanned_session(client, app, farmer, rows) -> str:
    created = await client.post("/api/v1/sessions", json={}, headers=farmer.headers)
    session_uuid = created.json()["uuid"]
    await record(app, await user_id_of(client, farmer), session_uuid, rows)
    return session_uuid


# ── JSON report ───────────────────────────────────────────────────────────


async def test_report_counts_figs_healthy_and_aflatoxin(client, app, farmer):
    rows = [("Healthy", 0.95)] * 8 + [("Aflatoxin", 0.88)] * 2
    session_uuid = await scanned_session(client, app, farmer, rows)

    response = await client.get(
        f"/api/v1/sessions/{session_uuid}/report", headers=farmer.headers
    )

    assert response.status_code == 200
    throughput = response.json()["throughput"]
    assert throughput["total_figs"] == 10
    assert throughput["healthy_count"] == 8
    assert throughput["aflatoxin_count"] == 2
    assert throughput["defect_rate_pct"] == 20.0


async def test_report_includes_model_analysis(client, app, farmer):
    rows = [("Healthy", 0.95)] * 5 + [("Aflatoxin", 0.60)] * 5
    session_uuid = await scanned_session(client, app, farmer, rows)

    analysis = (
        await client.get(f"/api/v1/sessions/{session_uuid}/report", headers=farmer.headers)
    ).json()["analysis"]

    assert analysis["mean_confidence"] == 0.775
    # Five figs scored below the 70% review threshold.
    assert analysis["low_confidence_count"] == 5
    assert analysis["low_confidence_pct"] == 50.0
    assert sum(b["count"] for b in analysis["confidence_histogram"]) == 10


async def test_report_breaks_down_by_class(client, app, farmer):
    rows = [("Healthy", 0.9)] * 7 + [("Aflatoxin", 0.8)] * 3
    session_uuid = await scanned_session(client, app, farmer, rows)

    per_class = (
        await client.get(f"/api/v1/sessions/{session_uuid}/report", headers=farmer.headers)
    ).json()["analysis"]["per_class"]

    by_decision = {row["decision"]: row for row in per_class}

    assert by_decision["Healthy"]["count"] == 7
    assert by_decision["Aflatoxin"]["count"] == 3
    assert by_decision["Aflatoxin"]["share_pct"] == 30.0


async def test_report_states_the_threshold_the_session_ran_at(client, app, farmer):
    """Figs scored under a different threshold are not comparable, so the report must say
    which one produced these numbers."""
    created = await client.post(
        "/api/v1/sessions", json={"conf_threshold": 0.77}, headers=farmer.headers
    )
    session_uuid = created.json()["uuid"]

    analysis = (
        await client.get(f"/api/v1/sessions/{session_uuid}/report", headers=farmer.headers)
    ).json()["analysis"]

    assert analysis["conf_threshold_used"] == 0.77


async def test_estimated_mass_is_returned_when_weight_is_supplied(client, app, farmer):
    session_uuid = await scanned_session(client, app, farmer, [("Healthy", 0.9)] * 100)

    response = await client.get(
        f"/api/v1/sessions/{session_uuid}/report?fig_weight_g=10",
        headers=farmer.headers,
    )

    assert response.json()["throughput"]["estimated_mass_g"] == 1000.0


async def test_mass_is_null_without_a_weight(client, app, farmer):
    session_uuid = await scanned_session(client, app, farmer, [("Healthy", 0.9)] * 5)

    response = await client.get(
        f"/api/v1/sessions/{session_uuid}/report", headers=farmer.headers
    )

    assert response.json()["throughput"]["estimated_mass_g"] is None


async def test_report_flags_a_low_confidence_batch(client, app, farmer):
    session_uuid = await scanned_session(client, app, farmer, [("Healthy", 0.55)] * 40)

    notes = (
        await client.get(f"/api/v1/sessions/{session_uuid}/report", headers=farmer.headers)
    ).json()["notes"]

    assert any("UV lighting" in note for note in notes)


async def test_empty_session_reports_zeroes_not_an_error(client, farmer):
    created = await client.post("/api/v1/sessions", json={}, headers=farmer.headers)

    response = await client.get(
        f"/api/v1/sessions/{created.json()['uuid']}/report", headers=farmer.headers
    )

    assert response.status_code == 200
    assert response.json()["throughput"]["total_figs"] == 0
    assert response.json()["throughput"]["defect_rate_pct"] == 0.0


async def test_report_of_unknown_session_is_404(client, farmer):
    response = await client.get(
        f"/api/v1/sessions/{uuid_module.uuid4()}/report", headers=farmer.headers
    )

    assert response.status_code == 404


async def test_report_of_another_farmers_session_is_404(client, app, farmer, neighbour):
    session_uuid = await scanned_session(client, app, farmer, [("Healthy", 0.9)])

    response = await client.get(
        f"/api/v1/sessions/{session_uuid}/report", headers=neighbour.headers
    )

    assert response.status_code == 404


async def test_report_requires_authentication(client, app, farmer):
    session_uuid = await scanned_session(client, app, farmer, [("Healthy", 0.9)])

    assert (await client.get(f"/api/v1/sessions/{session_uuid}/report")).status_code == 401


# ── PDF ───────────────────────────────────────────────────────────────────


async def test_pdf_is_a_pdf(client, app, farmer):
    session_uuid = await scanned_session(
        client, app, farmer, [("Healthy", 0.95)] * 6 + [("Aflatoxin", 0.85)] * 2
    )

    response = await client.get(
        f"/api/v1/sessions/{session_uuid}/report.pdf", headers=farmer.headers
    )

    assert response.status_code == 200
    assert response.headers["content-type"] == "application/pdf"
    assert response.content.startswith(b"%PDF-"), "response is not a PDF document"
    assert response.content.rstrip().endswith(b"%%EOF"), "PDF is truncated"


async def test_pdf_is_offered_as_a_download_named_after_the_batch(client, app, farmer):
    session_uuid = await scanned_session(client, app, farmer, [("Healthy", 0.9)])

    detail = await client.get(f"/api/v1/sessions/{session_uuid}", headers=farmer.headers)
    batch_id = detail.json()["session"]["batch_id"]

    response = await client.get(
        f"/api/v1/sessions/{session_uuid}/report.pdf", headers=farmer.headers
    )

    assert "attachment" in response.headers["content-disposition"]
    assert f"{batch_id}_report.pdf" in response.headers["content-disposition"]


async def test_pdf_renders_for_an_empty_session(client, farmer):
    """A farmer who stops a session having scanned nothing still gets a document, not a 500."""
    created = await client.post("/api/v1/sessions", json={}, headers=farmer.headers)

    response = await client.get(
        f"/api/v1/sessions/{created.json()['uuid']}/report.pdf", headers=farmer.headers
    )

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")


async def test_pdf_handles_a_turkish_device_label(client, app, farmer):
    """ReportLab's built-in fonts are Latin-1, which lacks ğ, ı, ş and İ — the label would
    render as blanks in the document a farmer hands to a buyer."""
    created = await client.post(
        "/api/v1/sessions",
        json={"device_label": "Sarıgöl kurutma sahası — ışık"},
        headers=farmer.headers,
    )
    session_uuid = created.json()["uuid"]
    await record(app, await user_id_of(client, farmer), session_uuid, [("Healthy", 0.9)])

    response = await client.get(
        f"/api/v1/sessions/{session_uuid}/report.pdf", headers=farmer.headers
    )

    assert response.status_code == 200
    assert response.content.startswith(b"%PDF-")


async def test_pdf_of_another_farmers_session_is_404(client, app, farmer, neighbour):
    session_uuid = await scanned_session(client, app, farmer, [("Healthy", 0.9)])

    response = await client.get(
        f"/api/v1/sessions/{session_uuid}/report.pdf", headers=neighbour.headers
    )

    assert response.status_code == 404


# ── Range aggregate ───────────────────────────────────────────────────────


async def test_range_report_aggregates_across_sessions(client, app, farmer):
    for _ in range(2):
        session_uuid = await scanned_session(
            client, app, farmer, [("Healthy", 0.9)] * 4 + [("Aflatoxin", 0.9)]
        )
        await client.post(f"/api/v1/sessions/{session_uuid}/stop", headers=farmer.headers)

    response = await client.get("/api/v1/reports/range", headers=farmer.headers)

    assert response.status_code == 200
    body = response.json()
    assert body["sessions"] == 2
    assert body["total_figs"] == 10
    assert body["aflatoxin_count"] == 2
    assert body["defect_rate_pct"] == 20.0


async def test_range_report_excludes_other_farmers(client, app, farmer, neighbour):
    session_uuid = await scanned_session(client, app, farmer, [("Healthy", 0.9)] * 3)
    await client.post(f"/api/v1/sessions/{session_uuid}/stop", headers=farmer.headers)

    response = await client.get("/api/v1/reports/range", headers=neighbour.headers)

    assert response.json()["total_figs"] == 0
    assert response.json()["sessions"] == 0


async def test_range_report_respects_the_window(client, app, farmer):
    session_uuid = await scanned_session(client, app, farmer, [("Healthy", 0.9)] * 3)
    await client.post(f"/api/v1/sessions/{session_uuid}/stop", headers=farmer.headers)

    long_ago = (datetime.now(UTC) - timedelta(days=90)).isoformat()
    older = (datetime.now(UTC) - timedelta(days=60)).isoformat()

    response = await client.get(
        f"/api/v1/reports/range?start={quote(long_ago)}&end={quote(older)}", headers=farmer.headers
    )

    assert response.json()["total_figs"] == 0


async def test_range_report_rejects_a_reversed_window(client, farmer):
    now = datetime.now(UTC)
    response = await client.get(
        f"/api/v1/reports/range?start={quote(now.isoformat())}"
        f"&end={quote((now - timedelta(days=1)).isoformat())}",
        headers=farmer.headers,
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_range_report_rejects_an_unbounded_window(client, farmer):
    now = datetime.now(UTC)
    response = await client.get(
        f"/api/v1/reports/range?start={quote((now - timedelta(days=5000)).isoformat())}"
        f"&end={quote(now.isoformat())}",
        headers=farmer.headers,
    )

    assert response.status_code == 400


async def test_range_report_accepts_naive_timestamps(client, farmer):
    """Query parameters routinely arrive without an offset; comparing a naive value against a
    TIMESTAMPTZ column raises on PostgreSQL."""
    now = datetime.now(UTC).replace(tzinfo=None)

    response = await client.get(
        f"/api/v1/reports/range?start={quote((now - timedelta(days=1)).isoformat())}"
        f"&end={quote(now.isoformat())}",
        headers=farmer.headers,
    )

    assert response.status_code == 200


async def test_range_report_requires_authentication(client):
    assert (await client.get("/api/v1/reports/range")).status_code == 401
