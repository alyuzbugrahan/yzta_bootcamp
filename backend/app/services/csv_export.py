"""Streaming CSV export.

Ported from ``SessionDAO.export_to_csv`` (data/session_dao.py:80), which fetched every row,
wrote a file under ``data/exports/`` and returned its path for a dialog to display. A path on
the server's disk means nothing to a browser, so rows are streamed straight into the response
and ``exports_dir`` is retired.

The column order and the UTF-8 BOM are preserved: the BOM is what makes Excel open a
Turkish-locale CSV without mangling non-ASCII text, and existing users' spreadsheets expect
these headers.
"""

from __future__ import annotations

import csv
import io
from collections.abc import AsyncIterator

from app.infra.repositories.session_repository import ExportRow

CSV_HEADERS = [
    "Fig_ID",
    "Batch_ID",
    "Timestamp",
    "Decision",
    "Confidence",
    "Latency_ms",
    "Image_Path",
]

BOM = "﻿"


def _format(row: ExportRow, image_url_base: str | None) -> list[str]:
    # The URL addresses the inspection, not the storage key: the endpoint re-checks ownership
    # on every fetch, whereas a key in a spreadsheet would be a bare pointer into the bucket.
    if row.image_key and image_url_base:
        image_ref = f"{image_url_base.rstrip('/')}/inspections/{row.inspection_id}/image"
    elif row.image_key:
        image_ref = row.image_key
    else:
        image_ref = ""

    return [
        str(row.fig_seq),
        row.batch_id,
        row.timestamp.isoformat(),
        row.decision,
        f"{row.confidence:.4f}",
        f"{row.latency_ms:.1f}",
        image_ref,
    ]


async def stream_csv(
    rows: AsyncIterator[ExportRow],
    image_url_base: str | None = None,
) -> AsyncIterator[str]:
    """Yield CSV text chunks, header first.

    Uses ``csv.writer`` against an in-memory buffer rather than joining strings by hand, so
    batch ids and decisions containing a comma or quote stay RFC 4180 correct.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    writer.writerow(CSV_HEADERS)
    yield BOM + buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)

    async for row in rows:
        writer.writerow(_format(row, image_url_base))
        yield buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)


def export_filename(batch_id: str) -> str:
    return f"{batch_id}.csv"
