"""Renders a list of report metrics into one PDF (bytes) via matplotlib + reportlab.

matplotlib draws each chart headlessly (Agg backend) to an in-memory PNG;
reportlab lays out title/date-range/chart/summary per metric into a single
document. No system packages needed beyond the two pip libraries.
"""
from __future__ import annotations

import io
from datetime import date

import matplotlib.pyplot as plt
from reportlab.lib.pagesizes import A4
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet

from .metrics import build_metric


def build_report_pdf(name: str, metric_codes: list[str], start: date, end: date) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
    )
    styles = getSampleStyleSheet()
    story = [
        Paragraph(name, styles["Title"]),
        Paragraph(f"Период: {start:%d.%m.%Y} — {end:%d.%m.%Y}", styles["Normal"]),
        Spacer(1, 0.6 * cm),
    ]

    for code in metric_codes:
        result = build_metric(code, start, end)
        img_buffer = io.BytesIO()
        result.figure.savefig(img_buffer, format="png", dpi=150, bbox_inches="tight")
        plt.close(result.figure)
        img_buffer.seek(0)

        story.append(Paragraph(result.title, styles["Heading2"]))
        story.append(RLImage(img_buffer, width=16 * cm, height=16 * cm * 0.45))
        story.append(Paragraph(result.summary, styles["Normal"]))
        story.append(Spacer(1, 0.8 * cm))

    doc.build(story)
    return buffer.getvalue()
