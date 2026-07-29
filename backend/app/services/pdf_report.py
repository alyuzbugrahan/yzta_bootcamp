"""Server-rendered PDF batch report.

A farmer sends this to a buyer, so it has to stand on its own: what was scanned, when, how much
was contaminated, and how far the numbers should be trusted.

ReportLab rather than a HTML-to-PDF engine. WeasyPrint would give nicer layout but drags Pango,
Cairo and GDK-Pixbuf into the runtime image for one endpoint; ReportLab is a pure pip install
and adds nothing to the container beyond a font.
"""

from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from app.core.logging import get_logger
from app.domain.report import SessionReport

log = get_logger(__name__)

# ReportLab's built-in fonts are Latin-1. That covers ç, ö and ü but not ğ, ı, ş or İ — so a
# Turkish device label would render as blanks in the very report a farmer hands to a buyer. A
# DejaVu TTF is registered when one is present; the Dockerfile installs fonts-dejavu-core.
_FONT_CANDIDATES = (
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
)

BODY_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"

ACCENT = colors.HexColor("#7c3aed")
DEFECT = colors.HexColor("#E24B4A")
HEALTHY = colors.HexColor("#1D9E75")
MUTED = colors.HexColor("#666666")


def _register_fonts() -> tuple[str, str]:
    """Register DejaVu if available. Falls back to Helvetica rather than failing the request."""
    regular, bold = _FONT_CANDIDATES

    if not Path(regular).exists():
        return "Helvetica", "Helvetica-Bold"

    try:
        pdfmetrics.registerFont(TTFont("DejaVuSans", regular))
        if Path(bold).exists():
            pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", bold))
            return "DejaVuSans", "DejaVuSans-Bold"
        return "DejaVuSans", "DejaVuSans"
    except Exception as exc:  # noqa: BLE001 - a missing glyph must not fail the download
        log.warning("font_registration_failed", error=str(exc))
        return "Helvetica", "Helvetica-Bold"


BODY_FONT, BOLD_FONT = _register_fonts()


def _styles():
    sheet = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "FigionTitle",
            parent=sheet["Title"],
            fontName=BOLD_FONT,
            fontSize=18,
            textColor=colors.HexColor("#1a1a1a"),
            spaceAfter=2 * mm,
        ),
        "subtitle": ParagraphStyle(
            "FigionSubtitle",
            parent=sheet["Normal"],
            fontName=BODY_FONT,
            fontSize=9,
            textColor=MUTED,
            spaceAfter=6 * mm,
        ),
        "heading": ParagraphStyle(
            "FigionHeading",
            parent=sheet["Heading2"],
            fontName=BOLD_FONT,
            fontSize=11,
            textColor=ACCENT,
            spaceBefore=5 * mm,
            spaceAfter=2 * mm,
        ),
        "body": ParagraphStyle(
            "FigionBody",
            parent=sheet["Normal"],
            fontName=BODY_FONT,
            fontSize=9,
            leading=13,
        ),
        "note": ParagraphStyle(
            "FigionNote",
            parent=sheet["Normal"],
            fontName=BODY_FONT,
            fontSize=9,
            leading=13,
            leftIndent=4 * mm,
            spaceAfter=2 * mm,
        ),
    }


def _table(rows: list[list[str]], widths: list[float], header: bool = True) -> Table:
    table = Table(rows, colWidths=widths, hAlign="LEFT")

    style = [
        ("FONTNAME", (0, 0), (-1, -1), BODY_FONT),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#1a1a1a")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -2), 0.25, colors.HexColor("#e5e5e5")),
    ]

    if header:
        style += [
            ("FONTNAME", (0, 0), (-1, 0), BOLD_FONT),
            ("TEXTCOLOR", (0, 0), (-1, 0), MUTED),
            ("LINEBELOW", (0, 0), (-1, 0), 0.75, colors.HexColor("#cccccc")),
        ]

    table.setStyle(TableStyle(style))
    return table


def _format_duration(seconds: float) -> str:
    total = int(seconds)
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _format_mass(grams: float | None) -> str:
    if grams is None:
        return "—"
    return f"{grams:.0f} g" if grams < 1000 else f"{grams / 1000:.2f} kg"


def render_session_report(report: SessionReport) -> bytes:
    """Render one batch report to PDF bytes.

    Synchronous and CPU-bound; the endpoint runs it on a worker thread so a large batch does
    not stall the event loop for every other connected farmer.
    """
    styles = _styles()
    buffer = io.BytesIO()

    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"Figion batch report — {report.batch_id}",
        author="Figion",
    )

    flow = []
    throughput = report.throughput
    analysis = report.analysis

    flow.append(Paragraph("Aflatoxin Inspection Report", styles["title"]))

    scanned = report.started_at.strftime("%Y-%m-%d %H:%M UTC")
    device = report.device_label or "unspecified device"
    status = " — SESSION STILL OPEN" if report.is_open else ""
    flow.append(Paragraph(f"{report.batch_id} · {scanned} · {device}{status}", styles["subtitle"]))

    # ── Throughput ────────────────────────────────────────────────────────
    flow.append(Paragraph("Batch totals", styles["heading"]))
    flow.append(
        _table(
            [
                ["Measure", "Value"],
                ["Figs scanned", f"{throughput.total_figs}"],
                ["Healthy", f"{throughput.healthy_count}"],
                ["Aflatoxin", f"{throughput.aflatoxin_count}"],
                ["Contamination rate", f"{throughput.defect_rate_pct:.2f}%"],
                ["Estimated mass", _format_mass(throughput.estimated_mass_g)],
                ["Duration", _format_duration(throughput.duration_seconds)],
                ["Throughput", f"{throughput.figs_per_minute:.1f} figs/min"],
            ],
            widths=[60 * mm, 40 * mm],
        )
    )

    # ── Model analysis ────────────────────────────────────────────────────
    flow.append(Paragraph("Model analysis", styles["heading"]))
    flow.append(
        Paragraph(
            "Statistics derived from the detector's own scores for this batch. "
            f"Confidence threshold in force: {analysis.conf_threshold_used:.0%}.",
            styles["body"],
        )
    )
    flow.append(Spacer(1, 3 * mm))

    flow.append(
        _table(
            [
                ["Class", "Count", "Share", "Mean conf.", "Lowest conf."],
                *[
                    [
                        row.decision,
                        f"{row.count}",
                        f"{row.share_pct:.1f}%",
                        f"{row.mean_confidence:.1%}" if row.count else "—",
                        f"{row.min_confidence:.1%}" if row.count else "—",
                    ]
                    for row in analysis.per_class
                ],
            ],
            widths=[35 * mm, 20 * mm, 20 * mm, 28 * mm, 28 * mm],
        )
    )
    flow.append(Spacer(1, 4 * mm))

    flow.append(
        _table(
            [
                ["Measure", "Value"],
                ["Mean confidence", f"{analysis.mean_confidence:.1%}"],
                ["Median confidence", f"{analysis.median_confidence:.1%}"],
                [
                    f"Below {analysis.low_confidence_threshold:.0%} confidence",
                    (
                        f"{analysis.low_confidence_count} "
                        f"({analysis.low_confidence_pct:.1f}%)"
                    ),
                ],
                ["Decision latency (median)", f"{analysis.latency_p50_ms:.0f} ms"],
                ["Decision latency (95th pct)", f"{analysis.latency_p95_ms:.0f} ms"],
            ],
            widths=[60 * mm, 40 * mm],
        )
    )

    # ── Distribution ──────────────────────────────────────────────────────
    if throughput.total_figs:
        flow.append(Paragraph("Confidence distribution", styles["heading"]))
        flow.append(
            _table(
                [
                    ["Confidence", "Figs", "Share"],
                    *[
                        [
                            bucket.label,
                            f"{bucket.count}",
                            f"{bucket.count / throughput.total_figs * 100:.1f}%",
                        ]
                        for bucket in analysis.confidence_histogram
                    ],
                ],
                widths=[35 * mm, 25 * mm, 25 * mm],
            )
        )

    # ── Notes ─────────────────────────────────────────────────────────────
    if report.notes:
        block = [Paragraph("What to check", styles["heading"])]
        block += [Paragraph(f"• {note}", styles["note"]) for note in report.notes]
        # Kept on one page: a heading stranded above a page break reads as an empty section.
        flow.append(KeepTogether(block))

    flow.append(Spacer(1, 8 * mm))
    flow.append(
        Paragraph(
            "Generated by Figion. Detection depends on UV illumination; figures from a batch "
            "scanned without a UV lamp are not meaningful.",
            ParagraphStyle(
                "Footer", fontName=BODY_FONT, fontSize=7.5, textColor=MUTED, leading=10
            ),
        )
    )

    document.build(flow)
    return buffer.getvalue()
