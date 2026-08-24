"""Renders a list of report metrics into one PDF (bytes) via matplotlib + reportlab.

matplotlib draws each chart headlessly (Agg backend) to an in-memory PNG;
reportlab lays out title/date-range/chart/summary per metric into a single
document with a custom branded canvas and elegant layout.
"""
from __future__ import annotations

import io
import os
from datetime import date, datetime

import matplotlib
import matplotlib.pyplot as plt
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas
from reportlab.platypus import (
    HRFlowable,
    Image as RLImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from .metrics import build_metric

# reportlab's built-in fonts (Helvetica etc.) have no Cyrillic glyphs, so
# Russian text renders as boxes. Reuse matplotlib's bundled DejaVu Sans
# (which does have Cyrillic) instead of shipping a separate font file.
_FONTS_DIR = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
pdfmetrics.registerFont(TTFont("DejaVuSans", os.path.join(_FONTS_DIR, "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", os.path.join(_FONTS_DIR, "DejaVuSans-Bold.ttf")))


def format_page_number(current: int, total: int) -> str:
    """Formats the page-number string shown in the document footer."""
    return f"Стр. {current} из {total}"


class NumberedCanvas(canvas.Canvas):
    """Two-pass canvas: buffers every page, then draws the branded header/
    footer (which needs the final page count for "Стр. X из Y") once the
    total is known. Standard reportlab idiom for page-count-aware footers."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_page_states = []

    def showPage(self):
        self._saved_page_states.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        num_pages = len(self._saved_page_states)
        for state in self._saved_page_states:
            self.__dict__.update(state)
            self.draw_page_decorations(num_pages)
            super().showPage()
        super().save()

    def draw_page_decorations(self, page_count: int) -> None:
        self.saveState()

        # Top accent bar
        self.setFillColor(colors.HexColor("#7c6cf6"))
        self.rect(0, 29.7 * cm - 0.4 * cm, 21.0 * cm, 0.4 * cm, fill=1, stroke=0)

        # Header text
        self.setFont("DejaVuSans-Bold", 8)
        self.setFillColor(colors.HexColor("#64748b"))
        self.drawString(2 * cm, 29.7 * cm - 0.9 * cm, "MARKETSHELPER")

        self.setFont("DejaVuSans", 8)
        self.drawRightString(21.0 * cm - 2 * cm, 29.7 * cm - 0.9 * cm, "Аналитический отчёт")

        # Footer line
        self.setStrokeColor(colors.HexColor("#e2e8f0"))
        self.setLineWidth(0.5)
        self.line(2 * cm, 1.5 * cm, 21.0 * cm - 2 * cm, 1.5 * cm)

        # Footer text
        self.setFont("DejaVuSans", 8)
        self.setFillColor(colors.HexColor("#64748b"))
        gen_time = datetime.now().strftime("%d.%m.%Y %H:%M")
        self.drawString(2 * cm, 1.0 * cm, f"MARKETSHELPER SaaS Platform • Сформировано: {gen_time}")

        page_str = format_page_number(self._pageNumber, page_count)
        self.drawRightString(21.0 * cm - 2 * cm, 1.0 * cm, page_str)

        self.restoreState()


def _create_summary_box(text: str, style: ParagraphStyle) -> Table:
    """Wraps a metric's summary text in a callout box with an accent left border."""
    p = Paragraph(text, style)
    table = Table([[p]], colWidths=[17 * cm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("LINELEFT", (0, 0), (0, -1), 3, colors.HexColor("#7c6cf6")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#e2e8f0")),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return table


def build_report_pdf(name: str, metric_codes: list[str], start: date, end: date) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=2 * cm,
        rightMargin=2 * cm,
        topMargin=1.8 * cm,
        bottomMargin=2.0 * cm,
    )

    styles = getSampleStyleSheet()

    title_style = ParagraphStyle(
        "DocTitle",
        parent=styles["Normal"],
        fontName="DejaVuSans-Bold",
        fontSize=18,
        leading=22,
        textColor=colors.HexColor("#0f172a"),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="DejaVuSans",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor("#475569"),
    )
    heading_style = ParagraphStyle(
        "MetricHeading",
        parent=styles["Normal"],
        fontName="DejaVuSans-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor("#1e293b"),
        spaceBefore=8,
        spaceAfter=6,
    )
    summary_style = ParagraphStyle(
        "MetricSummary",
        parent=styles["Normal"],
        fontName="DejaVuSans",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor("#334155"),
    )

    # Document header card
    period_str = f"Период анализа: {start:%d.%m.%Y} — {end:%d.%m.%Y}"
    header_content = [
        [Paragraph(name, title_style)],
        [Paragraph(period_str, subtitle_style)],
    ]
    header_table = Table(header_content, colWidths=[17 * cm])
    header_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f1f5f9")),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LINELEFT", (0, 0), (0, -1), 4, colors.HexColor("#7c6cf6")),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor("#cbd5e1")),
            ]
        )
    )

    story = [
        header_table,
        Spacer(1, 0.4 * cm),
    ]

    for idx, code in enumerate(metric_codes):
        if idx > 0:
            story.append(
                HRFlowable(
                    width="100%",
                    thickness=0.5,
                    color=colors.HexColor("#e2e8f0"),
                    spaceBefore=8,
                    spaceAfter=8,
                )
            )

        result = build_metric(code, start, end)
        img_buffer = io.BytesIO()
        result.figure.savefig(img_buffer, format="png", dpi=150, bbox_inches="tight")
        plt.close(result.figure)
        img_buffer.seek(0)

        story.append(Paragraph(result.title, heading_style))
        story.append(RLImage(img_buffer, width=17 * cm, height=17 * cm * 0.45))
        story.append(Spacer(1, 0.2 * cm))
        story.append(_create_summary_box(result.summary, summary_style))
        story.append(Spacer(1, 0.4 * cm))

    doc.build(story, canvasmaker=NumberedCanvas)
    return buffer.getvalue()
