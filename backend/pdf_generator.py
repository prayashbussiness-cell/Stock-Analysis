"""
pdf_generator.py

Generates a professional PDF report from the structured JSON research data
returned by Gemini (11 verdict-tagged sections + a final checklist/
recommendation + price-projection chart + scorecard donut). Each section's
body is markdown text (tables, bullet lists, bold) which is converted here
into reportlab flowables — this is a small, purpose-built markdown-to-PDF
converter, not a general one, tailored to the predictable shapes Gemini is
prompted to emit.
"""

import os
import re
from datetime import datetime

from reportlab.lib.pagesizes import A4
from reportlab.lib.units import mm
from reportlab.lib import colors
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.platypus import (
    SimpleDocTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
    HRFlowable,
    PageBreak,
)
from reportlab.graphics.shapes import Drawing, Wedge, Circle, String, PolyLine, Line

REPORTS_DIR = os.path.join(os.path.dirname(__file__), "reports")

POSITIVE_GREEN = colors.HexColor("#00802b")
WARNING_RED = colors.HexColor("#c4172a")
AMBER = colors.HexColor("#b8860b")
TEXT_LIGHT = colors.HexColor("#1a1a1a")
MUTED_GRAY = colors.HexColor("#666666")
LINE_GRAY = colors.HexColor("#cccccc")

FOOTER_TEXT = "AI STOCK RESEARCH TERMINAL // DEEP SEARCH REPORT"

RECOMMENDATION_LABELS = {
    "invest_now": "INVEST NOW",
    "wait": "WAIT / ACCUMULATE GRADUALLY",
    "avoid": "AVOID",
}


def _slugify(text: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "_", text.strip().lower()).strip("_")
    return slug or "report"


def _verdict_color(verdict: str):
    return POSITIVE_GREEN if (verdict or "").lower() == "positive" else WARNING_RED


def _verdict_symbol(verdict: str) -> str:
    return "PASS" if (verdict or "").lower() == "positive" else "FAIL"


def _recommendation_color(rec: str):
    rec = (rec or "").lower()
    if rec == "invest_now":
        return POSITIVE_GREEN
    if rec == "avoid":
        return WARNING_RED
    return AMBER


def _build_styles():
    styles = getSampleStyleSheet()
    mono = "Courier"
    mono_bold = "Courier-Bold"

    styles.add(ParagraphStyle(
        name="TermTitle", fontName=mono_bold, fontSize=20, leading=24,
        textColor=colors.black, alignment=TA_CENTER, spaceAfter=4,
    ))
    styles.add(ParagraphStyle(
        name="TermSubtitle", fontName=mono, fontSize=9, leading=12,
        textColor=MUTED_GRAY, alignment=TA_CENTER, spaceAfter=14,
    ))
    styles.add(ParagraphStyle(
        name="TickerHeading", fontName=mono_bold, fontSize=15, leading=18,
        textColor=colors.black, spaceBefore=18, spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        name="TickerSub", fontName=mono, fontSize=9.5, leading=13,
        textColor=MUTED_GRAY, spaceAfter=8,
    ))
    styles.add(ParagraphStyle(
        name="SectionHeading", fontName=mono_bold, fontSize=11.5, leading=14,
        textColor=colors.black, spaceBefore=12, spaceAfter=2,
    ))
    styles.add(ParagraphStyle(
        name="VerdictLine", fontName=mono_bold, fontSize=9, leading=12,
        spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="BodyMono", fontName=mono, fontSize=9.5, leading=14,
        textColor=TEXT_LIGHT, alignment=TA_LEFT, spaceAfter=6,
    ))
    styles.add(ParagraphStyle(
        name="BulletMono", fontName=mono, fontSize=9.5, leading=14,
        textColor=TEXT_LIGHT, leftIndent=12, spaceAfter=3,
    ))
    styles.add(ParagraphStyle(
        name="CellMono", fontName=mono, fontSize=8, leading=10.5,
        textColor=TEXT_LIGHT,
    ))
    styles.add(ParagraphStyle(
        name="CellMonoBold", fontName=mono_bold, fontSize=8, leading=10.5,
        textColor=colors.black,
    ))
    return styles


def _inline_md(text: str) -> str:
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*(?!\*)(.+?)\*(?!\*)", r"<i>\1</i>", text)
    return text


def _table_style():
    return TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e6e6e6")),
        ("GRID", (0, 0), (-1, -1), 0.6, LINE_GRAY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
    ])


def _markdown_to_flowables(markdown_text: str, styles) -> list:
    """
    Small, purpose-built markdown -> reportlab converter. Handles paragraphs,
    "- "/"* " bullet lists, **bold**/*italic*, and markdown pipe tables
    (a header row, a "---" separator row, then data rows).
    """
    flowables = []
    lines = (markdown_text or "").splitlines()
    n = len(lines)
    i = 0
    buffer = []

    def flush_paragraph():
        if buffer:
            text = " ".join(buffer).strip()
            if text:
                flowables.append(Paragraph(_inline_md(text), styles["BodyMono"]))
            buffer.clear()

    def is_table_row(line):
        return line.strip().startswith("|") and line.strip().endswith("|")

    def is_separator_row(line):
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        return all(re.fullmatch(r":?-{2,}:?", c or "-") for c in cells) if cells else False

    while i < n:
        raw = lines[i]
        line = raw.strip()

        if not line:
            flush_paragraph()
            i += 1
            continue

        if is_table_row(line) and i + 1 < n and is_separator_row(lines[i + 1]):
            flush_paragraph()
            header_cells = [c.strip() for c in line.strip("|").split("|")]
            rows = [[Paragraph(_inline_md(c), styles["CellMonoBold"]) for c in header_cells]]
            j = i + 2
            while j < n and is_table_row(lines[j].strip()):
                data_cells = [c.strip() for c in lines[j].strip().strip("|").split("|")]
                # pad/truncate to header width defensively
                data_cells = (data_cells + [""] * len(header_cells))[:len(header_cells)]
                rows.append([Paragraph(_inline_md(c), styles["CellMono"]) for c in data_cells])
                j += 1
            col_width = (170 * mm) / max(1, len(header_cells))
            tbl = Table(rows, colWidths=[col_width] * len(header_cells))
            tbl.setStyle(_table_style())
            flowables.append(Spacer(1, 2 * mm))
            flowables.append(tbl)
            flowables.append(Spacer(1, 3 * mm))
            i = j
            continue

        if line.startswith(("- ", "* ")):
            flush_paragraph()
            bullet_text = _inline_md(line[2:].strip())
            flowables.append(Paragraph(f"&bull;&nbsp;&nbsp;{bullet_text}", styles["BulletMono"]))
            i += 1
            continue

        buffer.append(line)
        i += 1

    flush_paragraph()
    return flowables


def _ticker_flowables(entry: dict, styles) -> list:
    flowables = []

    ticker = entry.get("ticker", "N/A")
    company = entry.get("company_name", "")
    exchange = entry.get("exchange", "")
    sector = entry.get("sector", "")
    price = entry.get("current_price", "N/A")
    currency = entry.get("currency", "")
    change = entry.get("change_percent", 0)
    mcap = entry.get("market_cap", "N/A")

    change_str = f"{'+' if change is not None and change >= 0 else ''}{change}%"
    is_live = bool(entry.get("price_is_live"))
    live_tag = "[LIVE]" if is_live else "[ESTIMATED]"
    flowables.append(Paragraph(f"[ {ticker} ] {company}", styles["TickerHeading"]))
    flowables.append(HRFlowable(width="100%", thickness=1, color=colors.black, spaceAfter=6))
    flowables.append(Paragraph(
        f"{exchange} &bull; {sector} &bull; PRICE: {price} {currency} "
        f"({change_str}) {live_tag} &bull; MKT CAP: {mcap}",
        styles["TickerSub"],
    ))
    if not is_live:
        est_style = ParagraphStyle(
            "estnote", fontName="Courier-Oblique", fontSize=7.5, leading=10, textColor=MUTED_GRAY, spaceAfter=4
        )
        flowables.append(Paragraph(
            "Live price lookup was unavailable for this ticker — price/change/market cap above are the model's estimate, not a real quote.",
            est_style,
        ))

    key_stats = entry.get("key_stats") or []
    if key_stats:
        stat_row = [Paragraph(_inline_md(s.get("label", "")), styles["CellMonoBold"]) for s in key_stats]
        val_row = [Paragraph(_inline_md(str(s.get("value", ""))), styles["CellMono"]) for s in key_stats]
        stat_table = Table([stat_row, val_row], colWidths=[(170 * mm) / max(1, len(key_stats))] * len(key_stats))
        stat_table.setStyle(_table_style())
        flowables.append(stat_table)
        flowables.append(Spacer(1, 4 * mm))

    for idx, section in enumerate(entry.get("sections", []), start=1):
        title = section.get("title", "Section")
        verdict = section.get("verdict", "")
        reason = section.get("verdict_reason", "")

        flowables.append(Paragraph(f"{idx:02d}. {title.upper()}", styles["SectionHeading"]))
        v_style = ParagraphStyle(
            "vline", parent=styles["VerdictLine"], textColor=_verdict_color(verdict)
        )
        flowables.append(Paragraph(
            f"{_verdict_symbol(verdict)} &mdash; {_inline_md(reason)}",
            v_style,
        ))
        flowables.extend(_markdown_to_flowables(section.get("content_markdown", ""), styles))

    # Final summary
    final = entry.get("final_summary", {})
    flowables.append(Paragraph("FINAL SUMMARY", styles["SectionHeading"]))
    flowables.append(HRFlowable(width="100%", thickness=0.75, color=LINE_GRAY, spaceAfter=6))

    checklist = final.get("checklist", [])
    if checklist:
        rows = [[
            Paragraph("SECTION", styles["CellMonoBold"]),
            Paragraph("VERDICT", styles["CellMonoBold"]),
        ]]
        for item in checklist:
            v = item.get("verdict", "")
            v_cell_style = ParagraphStyle(
                "cv", parent=styles["CellMono"], textColor=_verdict_color(v), fontName="Courier-Bold"
            )
            rows.append([
                Paragraph(_inline_md(item.get("section", "")), styles["CellMono"]),
                Paragraph(_verdict_symbol(v), v_cell_style),
            ])
        tbl = Table(rows, colWidths=[110 * mm, 60 * mm])
        tbl.setStyle(_table_style())
        flowables.append(tbl)
        flowables.append(Spacer(1, 4 * mm))

    rec = final.get("recommendation", "")
    rec_style = ParagraphStyle(
        "rec", fontName="Courier-Bold", fontSize=13, leading=16,
        textColor=_recommendation_color(rec), spaceAfter=6,
    )
    flowables.append(Paragraph(
        f"RECOMMENDATION: {RECOMMENDATION_LABELS.get(rec, rec.upper() or 'N/A')}", rec_style
    ))
    flowables.extend(_markdown_to_flowables(final.get("summary_text", ""), styles))

    disclaimer = entry.get("disclaimer", "")
    if disclaimer:
        flowables.append(Spacer(1, 3 * mm))
        disc_style = ParagraphStyle(
            "disc", fontName="Courier-Oblique", fontSize=8, leading=11, textColor=MUTED_GRAY
        )
        flowables.append(Paragraph(_inline_md(disclaimer), disc_style))

    # Section 11: Forward price projection (best case, 24 months)
    projection = entry.get("price_projection")
    if projection and projection.get("prices"):
        flowables.append(Paragraph("FORWARD PRICE PROJECTION — NEXT 24 MONTHS (BEST CASE)", styles["SectionHeading"]))
        flowables.append(_price_projection_drawing(projection["months"], projection["prices"]))
        pd_disc_style = ParagraphStyle(
            "pdisc", fontName="Courier-Oblique", fontSize=7.5, leading=10.5, textColor=MUTED_GRAY, spaceBefore=4, spaceAfter=6
        )
        flowables.append(Paragraph(_inline_md(projection.get("disclaimer", "")), pd_disc_style))
        for bullet in projection.get("reasoning", []):
            flowables.append(Paragraph(f"&bull;&nbsp;&nbsp;{_inline_md(bullet)}", styles["BulletMono"]))

    # Section 12: Scorecard donut
    scorecard = entry.get("scorecard")
    if scorecard and scorecard.get("sections"):
        flowables.append(Paragraph("SCORECARD", styles["SectionHeading"]))
        flowables.append(_scorecard_flowable(scorecard, styles))

    return flowables


def _price_projection_drawing(months: list, prices: list, width: float = 480, height: float = 220):
    """Hand-drawn monthly line chart: realistic up/down price path over the
    projection window, with axis labels. No external chart library needed —
    built directly from reportlab.graphics primitives."""
    margin_left, margin_right, margin_top, margin_bottom = 46, 14, 14, 26
    plot_w = width - margin_left - margin_right
    plot_h = height - margin_top - margin_bottom

    d = Drawing(width, height)

    lo, hi = min(prices), max(prices)
    pad = (hi - lo) * 0.08 or (hi * 0.05 or 1)
    lo -= pad
    hi += pad

    def xy(i, price):
        x = margin_left + (i / (len(prices) - 1)) * plot_w
        y = margin_bottom + ((price - lo) / (hi - lo)) * plot_h
        return x, y

    # gridlines + y labels (4 bands)
    for k in range(5):
        gy = margin_bottom + (k / 4) * plot_h
        d.add(Line(margin_left, gy, width - margin_right, gy, strokeColor=LINE_GRAY, strokeWidth=0.4))
        price_at = lo + (hi - lo) * (k / 4)
        d.add(String(margin_left - 6, gy - 3, f"{price_at:.0f}", fontName="Courier", fontSize=6.5,
                      fillColor=MUTED_GRAY, textAnchor="end"))

    # x-axis month labels every 3 months
    for i in range(0, len(months), 3):
        x, _ = xy(i, prices[i])
        d.add(String(x, margin_bottom - 12, f"M{months[i]}", fontName="Courier", fontSize=6.5,
                      fillColor=MUTED_GRAY, textAnchor="middle"))

    # axis lines
    d.add(Line(margin_left, margin_bottom, width - margin_right, margin_bottom, strokeColor=colors.black, strokeWidth=0.8))

    # price path
    points = []
    for i, p in enumerate(prices):
        x, y = xy(i, p)
        points.extend([x, y])
    d.add(PolyLine(points, strokeColor=POSITIVE_GREEN, strokeWidth=1.6))

    # start/end markers + labels
    x0, y0 = xy(0, prices[0])
    xN, yN = xy(len(prices) - 1, prices[-1])
    d.add(Circle(x0, y0, 2.2, fillColor=colors.black, strokeColor=None))
    d.add(Circle(xN, yN, 2.6, fillColor=POSITIVE_GREEN, strokeColor=None))
    d.add(String(x0, y0 + 6, f"{prices[0]:.0f}", fontName="Courier-Bold", fontSize=7, fillColor=colors.black))
    d.add(String(xN - 8, yN + 6, f"{prices[-1]:.0f}", fontName="Courier-Bold", fontSize=7, fillColor=POSITIVE_GREEN, textAnchor="end"))

    return d


def _scorecard_flowable(scorecard: dict, styles):
    """Donut chart (drawn as pie wedges with a punched-out center circle)
    showing each section's weight, colored green (pass) / red (fail), with
    the total score in the center — plus a legend table below it."""
    sections = scorecard.get("sections", [])
    total_score = scorecard.get("total_score", 0)
    max_score = scorecard.get("max_score", 100)

    size = 150
    cx, cy, r_outer, r_inner = size / 2, size / 2, size / 2 - 6, (size / 2 - 6) * 0.55
    d = Drawing(size, size)

    angle = 90.0  # start at top, sweep clockwise (i.e. decreasing angle)
    total_weight = sum(s.get("weight", 0) for s in sections) or 1
    for s in sections:
        sweep = 360.0 * (s.get("weight", 0) / total_weight)
        start = angle - sweep
        color = POSITIVE_GREEN if s.get("verdict") == "positive" else WARNING_RED
        d.add(Wedge(cx, cy, r_outer, start, angle, fillColor=color, strokeColor=colors.white, strokeWidth=1))
        angle = start

    # punch the donut hole
    d.add(Circle(cx, cy, r_inner, fillColor=colors.white, strokeColor=None))
    d.add(String(cx, cy + 4, str(total_score), fontName="Courier-Bold", fontSize=20,
                  fillColor=colors.black, textAnchor="middle"))
    d.add(String(cx, cy - 12, f"/ {max_score}", fontName="Courier", fontSize=8,
                  fillColor=MUTED_GRAY, textAnchor="middle"))

    legend_rows = [[
        Paragraph("SECTION", styles["CellMonoBold"]),
        Paragraph("WEIGHT", styles["CellMonoBold"]),
        Paragraph("RESULT", styles["CellMonoBold"]),
    ]]
    for s in sections:
        v = s.get("verdict", "negative")
        v_style = ParagraphStyle("scv", parent=styles["CellMono"], textColor=_verdict_color(v), fontName="Courier-Bold")
        legend_rows.append([
            Paragraph(_inline_md(s.get("title", "")), styles["CellMono"]),
            Paragraph(str(s.get("weight", 0)), styles["CellMono"]),
            Paragraph(_verdict_symbol(v), v_style),
        ])
    legend = Table(legend_rows, colWidths=[65 * mm, 18 * mm, 32 * mm])
    legend.setStyle(_table_style())

    wrapper = Table([[d, legend]], colWidths=[55 * mm, 115 * mm])
    wrapper.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (0, 0), 0),
        ("LEFTPADDING", (1, 0), (1, 0), 10),
    ]))
    return wrapper


def _add_page_number(canvas, doc):
    canvas.saveState()
    canvas.setFont("Courier", 7.5)
    canvas.setFillColor(MUTED_GRAY)
    canvas.drawString(20 * mm, 12 * mm, FOOTER_TEXT)
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"Page {doc.page}")
    canvas.setStrokeColor(LINE_GRAY)
    canvas.line(20 * mm, 16 * mm, A4[0] - 20 * mm, 16 * mm)
    canvas.restoreState()


def generate_pdf(tickers: list, report_data: dict) -> str:
    """
    Build the PDF report and save it to reports/. Returns the filename.
    """
    os.makedirs(REPORTS_DIR, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    filename = f"{_slugify('_'.join(tickers))}_{timestamp}.pdf"
    filepath = os.path.join(REPORTS_DIR, filename)

    styles = _build_styles()
    doc = SimpleDocTemplate(
        filepath, pagesize=A4,
        topMargin=20 * mm, bottomMargin=22 * mm, leftMargin=20 * mm, rightMargin=20 * mm,
        title="AI Stock Research Terminal Report",
        author="AI Stock Research Terminal",
    )

    story = []
    story.append(Paragraph("STOCK RESEARCH TERMINAL", styles["TermTitle"]))
    generated_date = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    story.append(Paragraph(
        f"DEEP SEARCH REPORT &bull; GENERATED: {generated_date}",
        styles["TermSubtitle"],
    ))

    entries = report_data.get("tickers", [])
    for i, entry in enumerate(entries):
        story.extend(_ticker_flowables(entry, styles))
        if i < len(entries) - 1:
            story.append(PageBreak())

    doc.build(story, onFirstPage=_add_page_number, onLaterPages=_add_page_number)
    return filename
