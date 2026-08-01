"""AgroVision oturum raporunu Türkçe PDF olarak üretir."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

from app.core.logging import get_logger
from app.domain.report import SessionReport

log = get_logger(__name__)

_FONT_CANDIDATES = (
    (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ),
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
)

BODY_FONT = "Helvetica"
BOLD_FONT = "Helvetica-Bold"

ACCENT = colors.HexColor("#245e4c")
DARK = colors.HexColor("#173f34")
MUTED = colors.HexColor("#667570")
LINE = colors.HexColor("#dfe8e4")
SOFT = colors.HexColor("#f4f7f6")
DANGER = colors.HexColor("#c84e45")
HEALTHY = colors.HexColor("#1d8a62")
REPORT_TZ = timezone(timedelta(hours=3), name="TRT")


def _register_fonts() -> tuple[str, str]:
    for index, (regular, bold) in enumerate(_FONT_CANDIDATES):
        if not Path(regular).exists():
            continue

        regular_name = f"AgroVisionSans{index}"
        bold_name = f"AgroVisionSansBold{index}"
        try:
            pdfmetrics.registerFont(TTFont(regular_name, regular))
            if Path(bold).exists():
                pdfmetrics.registerFont(TTFont(bold_name, bold))
                return regular_name, bold_name
            return regular_name, regular_name
        except Exception as exc:  # noqa: BLE001
            log.warning("font_registration_failed", font=regular, error=str(exc))

    return "Helvetica", "Helvetica-Bold"


BODY_FONT, BOLD_FONT = _register_fonts()


def _styles() -> dict[str, ParagraphStyle]:
    sheet = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "AgroVisionTitle",
            parent=sheet["Title"],
            fontName=BOLD_FONT,
            fontSize=18,
            textColor=DARK,
            alignment=0,
            spaceAfter=2 * mm,
        ),
        "subtitle": ParagraphStyle(
            "AgroVisionSubtitle",
            parent=sheet["Normal"],
            fontName=BODY_FONT,
            fontSize=9,
            textColor=MUTED,
            leading=13,
            spaceAfter=6 * mm,
        ),
        "heading": ParagraphStyle(
            "AgroVisionHeading",
            parent=sheet["Heading2"],
            fontName=BOLD_FONT,
            fontSize=11,
            textColor=ACCENT,
            spaceBefore=5 * mm,
            spaceAfter=2 * mm,
        ),
        "body": ParagraphStyle(
            "AgroVisionBody",
            parent=sheet["Normal"],
            fontName=BODY_FONT,
            fontSize=9,
            textColor=colors.HexColor("#263d35"),
            leading=13,
        ),
        "footer": ParagraphStyle(
            "AgroVisionFooter",
            parent=sheet["Normal"],
            fontName=BODY_FONT,
            fontSize=7.5,
            textColor=MUTED,
            leading=10,
        ),
    }


def _table(rows: list[list[str]], widths: list[float]) -> Table:
    table = Table(rows, colWidths=widths, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("FONTNAME", (0, 0), (-1, -1), BODY_FONT),
                ("FONTSIZE", (0, 0), (-1, -1), 9),
                ("TEXTCOLOR", (0, 0), (-1, -1), colors.HexColor("#1a2d27")),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 8),
                ("RIGHTPADDING", (0, 0), (-1, -1), 8),
                ("BACKGROUND", (0, 0), (0, -1), SOFT),
                ("FONTNAME", (0, 0), (0, -1), BOLD_FONT),
                ("TEXTCOLOR", (0, 0), (0, -1), MUTED),
                ("GRID", (0, 0), (-1, -1), 0.45, LINE),
            ]
        )
    )
    return table


def _format_duration(seconds: float) -> str:
    total = max(int(seconds), 0)
    hours, remainder = divmod(total, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.astimezone(REPORT_TZ).strftime("%d.%m.%Y %H:%M")


def _format_mass(grams: float | None) -> str:
    if grams is None:
        return "Gramaj girilmedi"
    kilograms = grams / 1000
    return f"{kilograms:,.3f} kg".replace(",", "X").replace(".", ",").replace("X", ".")


def _format_weight(weight_g: float | None) -> str:
    if weight_g is None:
        return "-"
    return f"{weight_g:g} g"


def _source_label(report: SessionReport) -> str:
    if report.count_source == "model":
        return "Model sonuçları"
    if report.manual_counts_applied:
        return "Kullanıcının girdiği adetler"
    return "Model sonuçları (manuel adet girişi bulunmuyor)"


def render_session_report(report: SessionReport) -> bytes:
    """Bir tarama oturumunun Türkçe özet raporunu PDF baytları olarak döndürür."""
    styles = _styles()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=18 * mm,
        rightMargin=18 * mm,
        topMargin=16 * mm,
        bottomMargin=16 * mm,
        title=f"AgroVision Oturum Raporu - {report.batch_id}",
        author="AgroVision",
    )

    throughput = report.throughput
    status = "Devam ediyor" if report.is_open else "Tamamlandı"
    device = report.device_label or "Belirtilmedi"
    generated_at = datetime.now(REPORT_TZ).strftime("%d.%m.%Y %H:%M")

    flow = [
        Paragraph("AgroVision Aflatoksin Kontrol Raporu", styles["title"]),
        Paragraph(
            f"Parti: <b>{report.batch_id}</b> &nbsp;&nbsp;|&nbsp;&nbsp; "
            f"Rapor oluşturma: {generated_at}",
            styles["subtitle"],
        ),
        Paragraph("Oturum Bilgileri", styles["heading"]),
        _table(
            [
                ["Parti adı", report.batch_id],
                ["Başlangıç", _format_datetime(report.started_at)],
                ["Bitiş", _format_datetime(report.ended_at)],
                ["Cihaz / açıklama", device],
                ["Oturum durumu", status],
                ["Rapor veri kaynağı", _source_label(report)],
                ["Bir ürün gramajı", _format_weight(report.fig_weight_g)],
            ],
            widths=[55 * mm, 105 * mm],
        ),
        Paragraph("Ürün Miktarları", styles["heading"]),
        _table(
            [
                ["Toplam ürün miktarı", f"{throughput.total_figs}"],
                ["Sağlıklı ürün miktarı", f"{throughput.healthy_count}"],
                ["Aflatoksinli ürün miktarı", f"{throughput.aflatoxin_count}"],
                ["Aflatoksin oranı", f"%{throughput.defect_rate_pct:.2f}".replace(".", ",")],
                ["Toplam ağırlık", _format_mass(throughput.estimated_mass_g)],
                ["Oturum süresi", _format_duration(throughput.duration_seconds)],
            ],
            widths=[55 * mm, 105 * mm],
        ),
        Spacer(1, 6 * mm),
    ]

    if throughput.aflatoxin_count:
        flow.append(
            Paragraph(
                "Bu raporda aflatoksinli olarak işaretlenen ürünler bulunmaktadır. "
                "Nihai ayırma ve kalite kararı yetkili kullanıcı tarafından doğrulanmalıdır.",
                ParagraphStyle(
                    "Warning",
                    parent=styles["body"],
                    textColor=DANGER,
                    borderColor=colors.HexColor("#ecc9c5"),
                    borderWidth=0.6,
                    borderPadding=8,
                    backColor=colors.HexColor("#fff4f2"),
                ),
            )
        )
    else:
        flow.append(
            Paragraph(
                "Seçilen veri kaynağına göre bu oturumda aflatoksinli ürün kaydı bulunmamaktadır.",
                ParagraphStyle(
                    "Success",
                    parent=styles["body"],
                    textColor=HEALTHY,
                    borderColor=colors.HexColor("#cce5d8"),
                    borderWidth=0.6,
                    borderPadding=8,
                    backColor=colors.HexColor("#f0faf5"),
                ),
            )
        )

    flow.extend(
        [
            Spacer(1, 10 * mm),
            Paragraph(
                "Bu rapor AgroVision tarafından oluşturulmuştur. Sonuçların güvenilirliği kamera "
                "konumu, UV aydınlatma ve kullanıcı tarafından yapılan manuel düzeltmelerden "
                "etkilenebilir.",
                styles["footer"],
            ),
        ]
    )

    document.build(flow)
    return buffer.getvalue()
