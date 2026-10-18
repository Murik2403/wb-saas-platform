"""Renders a list of report metrics into one PDF (bytes) via matplotlib + reportlab.

matplotlib draws each chart headlessly (Agg backend) to an in-memory PNG;
reportlab lays out title/date-range/chart/summary per metric into a single
document with a custom branded canvas and elegant layout.
"""
from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
from datetime import date, datetime
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

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
    KeepTogether,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

from .metrics import (
    COLOR_ACCENT,
    COLOR_GRID,
    COLOR_MUTED,
    COLOR_TEXT,
    build_metric,
)

# reportlab's built-in fonts (Helvetica etc.) have no Cyrillic glyphs, so
# Russian text renders as boxes. Reuse matplotlib's bundled DejaVu Sans
# (which does have Cyrillic) instead of shipping a separate font file.
_FONTS_DIR = os.path.join(matplotlib.get_data_path(), "fonts", "ttf")
pdfmetrics.registerFont(TTFont("DejaVuSans", os.path.join(_FONTS_DIR, "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DejaVuSans-Bold", os.path.join(_FONTS_DIR, "DejaVuSans-Bold.ttf")))


def format_page_number(current: int, total: int) -> str:
    """Formats the page-number string shown in the document footer."""
    return f"Стр. {current} из {total}"


def generation_timestamp() -> str:
    """Moscow-local generation time for the footer. The server OS clock is
    UTC -- every timestamp in this project is shown as Moscow-local (see
    report_scheduler.py's _now_msk_naive_iso), so a bare datetime.now()
    here would display 3 hours behind, as it did before this existed."""
    return datetime.now(ZoneInfo("Europe/Moscow")).strftime("%d.%m.%Y %H:%M")


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
        self.setFillColor(colors.HexColor(COLOR_ACCENT))
        self.rect(0, 29.7 * cm - 0.4 * cm, 21.0 * cm, 0.4 * cm, fill=1, stroke=0)

        # Header text
        self.setFont("DejaVuSans-Bold", 8)
        self.setFillColor(colors.HexColor(COLOR_MUTED))
        self.drawString(2 * cm, 29.7 * cm - 0.9 * cm, "MARKETSHELPER")

        self.setFont("DejaVuSans", 8)
        self.drawRightString(21.0 * cm - 2 * cm, 29.7 * cm - 0.9 * cm, "Аналитический отчёт")

        # Footer line
        self.setStrokeColor(colors.HexColor(COLOR_GRID))
        self.setLineWidth(0.5)
        self.line(2 * cm, 1.5 * cm, 21.0 * cm - 2 * cm, 1.5 * cm)

        # Footer text
        self.setFont("DejaVuSans", 8)
        self.setFillColor(colors.HexColor(COLOR_MUTED))
        self.drawString(2 * cm, 1.0 * cm, f"MARKETSHELPER SaaS Platform • Сформировано: {generation_timestamp()}")

        page_str = format_page_number(self._pageNumber, page_count)
        self.drawRightString(21.0 * cm - 2 * cm, 1.0 * cm, page_str)

        self.restoreState()


def _create_summary_box(text: str, style: ParagraphStyle) -> Table:
    """Wraps a metric's summary text in a callout box with an accent left border."""
    p = Paragraph(escape(text), style)
    table = Table([[p]], colWidths=[17 * cm])
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("LINELEFT", (0, 0), (0, -1), 3, colors.HexColor(COLOR_ACCENT)),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(COLOR_GRID)),
                ("TOPPADDING", (0, 0), (-1, -1), 6),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                ("LEFTPADDING", (0, 0), (-1, -1), 10),
                ("RIGHTPADDING", (0, 0), (-1, -1), 10),
            ]
        )
    )
    return table


def build_report_pdf(name: str, metric_codes: list[str], start: date, end: date) -> bytes:
    """Render one PDF inside the shared memory budget (see reports/pdf_budget.py).

    Callers that already hold a slot (the report worker) pass straight through; any other caller waits up to
    WB_SAAS_PDF_WAIT_SECONDS for a free slot and gets PdfBusy instead of rendering over the budget.
    """
    from reports.pdf_budget import pdf_slot
    try:
        wait = float(os.environ.get("WB_SAAS_PDF_WAIT_SECONDS", "120"))
    except ValueError:
        wait = 120.0
    with pdf_slot(wait=wait):
        return _render_report_pdf(name, metric_codes, start, end)


def _render_report_pdf(name: str, metric_codes: list[str], start: date, end: date) -> bytes:
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
        textColor=colors.HexColor(COLOR_TEXT),
        spaceAfter=4,
    )
    subtitle_style = ParagraphStyle(
        "DocSubtitle",
        parent=styles["Normal"],
        fontName="DejaVuSans",
        fontSize=9,
        leading=13,
        textColor=colors.HexColor(COLOR_MUTED),
    )
    heading_style = ParagraphStyle(
        "MetricHeading",
        parent=styles["Normal"],
        fontName="DejaVuSans-Bold",
        fontSize=12,
        leading=16,
        textColor=colors.HexColor(COLOR_TEXT),
        spaceBefore=8,
        spaceAfter=6,
    )
    summary_style = ParagraphStyle(
        "MetricSummary",
        parent=styles["Normal"],
        fontName="DejaVuSans",
        fontSize=8.5,
        leading=12,
        textColor=colors.HexColor(COLOR_TEXT),
    )

    # Document header card
    period_str = f"Период анализа: {start:%d.%m.%Y} — {end:%d.%m.%Y}"
    header_content = [
        [Paragraph(escape(name), title_style)],
        [Paragraph(escape(period_str), subtitle_style)],
    ]
    header_table = Table(header_content, colWidths=[17 * cm])
    header_table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f8fafc")),
                ("LEFTPADDING", (0, 0), (-1, -1), 12),
                ("RIGHTPADDING", (0, 0), (-1, -1), 12),
                ("TOPPADDING", (0, 0), (-1, -1), 10),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
                ("LINELEFT", (0, 0), (0, -1), 4, colors.HexColor(COLOR_ACCENT)),
                ("BOX", (0, 0), (-1, -1), 0.5, colors.HexColor(COLOR_GRID)),
            ]
        )
    )

    story = [
        header_table,
        Spacer(1, 0.4 * cm),
    ]

    isolated = os.environ.get("WB_SAAS_REPORT_ISOLATED_METRICS", "0") == "1"
    isolated_tmp = tempfile.TemporaryDirectory(prefix="marketshelper-report-") if isolated else None
    isolated_dir = isolated_tmp.name if isolated_tmp else ""
    if isolated:
        # one isolated process for the whole report (reports/report_worker.py): imports and the dashboard are done once
        completed = subprocess.run(
            [sys.executable, "-m", "reports.report_worker", start.isoformat(), end.isoformat(), isolated_dir, *metric_codes],
            capture_output=True,
            text=True,
            timeout=600,
            check=False,
        )
        if completed.returncode != 0:
            isolated_tmp.cleanup()
            detail = (completed.stderr or completed.stdout or "неизвестная ошибка").strip()
            raise RuntimeError(f"Раздел отчёта не сформирован: {detail[-800:]}")

    for idx, code in enumerate(metric_codes):
        if idx > 0:
            story.append(
                HRFlowable(
                    width="100%",
                    thickness=0.5,
                    color=colors.HexColor(COLOR_GRID),
                    spaceBefore=8,
                    spaceAfter=8,
                )
            )

        if isolated:
            image_bytes = open(os.path.join(isolated_dir, f"{idx}.png"), "rb").read()
            payload = json.loads(open(os.path.join(isolated_dir, f"{idx}.json"), encoding="utf-8").read())
            result_title = str(payload["title"])
            result_summary = str(payload["summary"])
            img_buffer = io.BytesIO(image_bytes)
        else:
            result = build_metric(code, start, end)
            img_buffer = io.BytesIO()
            result.figure.savefig(img_buffer, format="png", dpi=150, bbox_inches="tight")
            plt.close(result.figure)
            img_buffer.seek(0)
            result_title = result.title
            result_summary = result.summary

        metric_story = [
            Paragraph(escape(result_title), heading_style),
            RLImage(img_buffer, width=17 * cm, height=17 * cm * 0.45),
            Spacer(1, 0.2 * cm),
            _create_summary_box(result_summary, summary_style),
            Spacer(1, 0.4 * cm),
        ]
        story.append(KeepTogether(metric_story))

    try:
        doc.build(story, canvasmaker=NumberedCanvas)
    finally:
        if isolated_tmp is not None:
            isolated_tmp.cleanup()
    return buffer.getvalue()
