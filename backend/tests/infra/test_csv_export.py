"""CSV export: column compatibility with the desktop file, and RFC 4180 correctness."""

from __future__ import annotations

import csv
import io

from app.domain.models import Detection, InspectionResult
from app.services.csv_export import BOM, CSV_HEADERS, stream_csv


def result(decision: str = "Healthy", confidence: float = 0.9, latency: float = 50.0):
    return InspectionResult(
        decision=decision,
        confidence=confidence,
        detection=Detection(decision, confidence, (0.2, 0.2, 0.6, 0.6)),
        latency_ms=latency,
    )


async def collect(rows_iter, image_url_base=None) -> str:
    return "".join([chunk async for chunk in stream_csv(rows_iter, image_url_base)])


async def test_headers_match_the_desktop_export(sessions, farmer):
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)

    text = await collect(sessions.iter_export_rows(farmer.id, scan.uuid))

    first_line = text.lstrip(BOM).splitlines()[0]
    assert first_line.split(",") == CSV_HEADERS


async def test_starts_with_a_bom(sessions, farmer):
    """Excel needs the BOM to read a UTF-8 CSV without mangling non-ASCII text."""
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)

    text = await collect(sessions.iter_export_rows(farmer.id, scan.uuid))

    assert text.startswith(BOM)


async def test_rows_are_in_fig_order(sessions, inspections, farmer, db):
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)
    for decision in ("Healthy", "Aflatoxin", "Healthy"):
        await inspections.record(scan.id, result(decision))
    await db.commit()

    text = await collect(sessions.iter_export_rows(farmer.id, scan.uuid))
    rows = list(csv.reader(io.StringIO(text.lstrip(BOM))))[1:]

    assert [row[0] for row in rows] == ["1", "2", "3"]
    assert [row[3] for row in rows] == ["Healthy", "Aflatoxin", "Healthy"]


async def test_numeric_formatting_matches_the_desktop(sessions, inspections, farmer, db):
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)
    await inspections.record(scan.id, result("Healthy", 0.87654, 91.25))
    await db.commit()

    text = await collect(sessions.iter_export_rows(farmer.id, scan.uuid))
    row = list(csv.reader(io.StringIO(text.lstrip(BOM))))[1]

    assert row[4] == "0.8765"
    assert row[5] == "91.2"


async def test_commas_in_a_field_are_quoted(sessions, inspections, farmer, db):
    """The desktop writer used ``csv.writer`` too; hand-joining would corrupt such a file."""
    scan = await sessions.create(farmer.id, "BATCH,WITH,COMMAS", 0.5)
    await inspections.record(scan.id, result())
    await db.commit()

    text = await collect(sessions.iter_export_rows(farmer.id, scan.uuid))
    rows = list(csv.reader(io.StringIO(text.lstrip(BOM))))

    assert rows[1][1] == "BATCH,WITH,COMMAS"


async def test_image_key_becomes_an_api_url(sessions, inspections, farmer, db):
    """A server filesystem path is meaningless to a browser; the export carries a URL.

    The URL addresses the *inspection*, not the storage key. That endpoint re-checks ownership
    on every fetch, whereas a raw key in a spreadsheet would be a bare pointer into the bucket
    — and a spreadsheet is exactly the kind of thing that gets forwarded to a buyer.
    """
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)
    recorded = await inspections.record(
        scan.id, result(), image_key="u1/BATCH_A/fig_0001_Healthy.jpg"
    )
    await db.commit()

    text = await collect(
        sessions.iter_export_rows(farmer.id, scan.uuid),
        image_url_base="https://agrovision.example/api/v1",
    )
    row = list(csv.reader(io.StringIO(text.lstrip(BOM))))[1]

    assert row[6] == f"https://agrovision.example/api/v1/inspections/{recorded.id}/image"


async def test_missing_image_key_is_blank(sessions, inspections, farmer, db):
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)
    await inspections.record(scan.id, result())
    await db.commit()

    text = await collect(sessions.iter_export_rows(farmer.id, scan.uuid))
    row = list(csv.reader(io.StringIO(text.lstrip(BOM))))[1]

    assert row[6] == ""


async def test_export_of_another_farmers_session_is_empty(
    sessions, inspections, farmer, other_farmer, db
):
    scan = await sessions.create(farmer.id, "BATCH_A", 0.5)
    await inspections.record(scan.id, result())
    await db.commit()

    text = await collect(sessions.iter_export_rows(other_farmer.id, scan.uuid))
    rows = list(csv.reader(io.StringIO(text.lstrip(BOM))))

    assert len(rows) == 1, "only the header should be produced"
