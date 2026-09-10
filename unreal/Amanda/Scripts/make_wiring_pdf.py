"""Turn PRESENCE-WIRING.md into something printable.

A wiring sheet is read next to a keyboard with both hands busy, which is one of
the few genuine cases for paper. This is a deliberately small Markdown subset --
headings, paragraphs, lists, tables, code blocks, rules -- rendered for A4.

    .venv/Scripts/python tools/../../unreal/Amanda/Scripts/make_wiring_pdf.py

Needs reportlab: pip install reportlab
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (
    HRFlowable,
    ListFlowable,
    ListItem,
    PageBreak,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

SOURCE = Path(__file__).resolve().parents[2] / "PRESENCE-WIRING.md"
OUTPUT = Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE.with_suffix(".pdf")

INK = colors.HexColor("#1a1a1a")
QUIET = colors.HexColor("#5a5a5a")
RULE = colors.HexColor("#c8c8c8")
PANEL = colors.HexColor("#f4f4f2")


def styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "title", parent=base["Title"], fontName="Helvetica-Bold",
            fontSize=19, leading=23, textColor=INK, alignment=TA_LEFT,
            spaceAfter=2 * mm,
        ),
        "h2": ParagraphStyle(
            "h2", parent=base["Heading2"], fontName="Helvetica-Bold",
            fontSize=13, leading=16, textColor=INK,
            spaceBefore=6 * mm, spaceAfter=2 * mm,
        ),
        "h3": ParagraphStyle(
            "h3", parent=base["Heading3"], fontName="Helvetica-Bold",
            fontSize=11, leading=14, textColor=INK,
            spaceBefore=4 * mm, spaceAfter=1.5 * mm,
        ),
        "body": ParagraphStyle(
            "body", parent=base["BodyText"], fontName="Helvetica",
            fontSize=9.5, leading=13.5, textColor=INK, spaceAfter=2.5 * mm,
        ),
        "code": ParagraphStyle(
            "code", parent=base["Code"], fontName="Courier",
            fontSize=8.5, leading=11.5, textColor=INK,
        ),
        "cell": ParagraphStyle(
            "cell", parent=base["BodyText"], fontName="Helvetica",
            fontSize=8.5, leading=11, textColor=INK, spaceAfter=0,
        ),
        "cellhead": ParagraphStyle(
            "cellhead", parent=base["BodyText"], fontName="Helvetica-Bold",
            fontSize=8.5, leading=11, textColor=INK, spaceAfter=0,
        ),
    }


#: Typographic characters the Markdown uses, and their ASCII equivalents.
#:
#: The PDF's standard fonts do not carry all of these, and a missing glyph
#: prints as a blank or a box -- on a sheet whose whole purpose is to be read
#: next to a keyboard. An arrow that has become a black rectangle in a wiring
#: diagram is worse than a hyphen.
ASCII = {
    "—": " - ",    # em dash
    "–": "-",      # en dash
    "→": "->",     # right arrow
    "±": "+/-",    # plus-minus
    "−": "-",      # true minus
    "°": " deg",   # degree
    "‘": "'", "’": "'",
    "“": '"', "”": '"',
    "…": "...",
}


def to_ascii(text: str) -> str:
    for character, replacement in ASCII.items():
        text = text.replace(character, replacement)
    return text


def inline(text: str) -> str:
    """Markdown emphasis and code spans to reportlab's mini-HTML."""
    text = to_ascii(text)
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"`([^`]+)`", r'<font face="Courier" size="8.5">\1</font>', text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    return text


def table_from(rows, style):
    """A markdown table, minus its separator row."""
    header, *body = [r for r in rows if not set(r.replace("|", "").strip()) <= set("-: ")]
    columns = [c.strip() for c in header.strip("|").split("|")]
    data = [[Paragraph(inline(c), style["cellhead"]) for c in columns]]
    for row in body:
        cells = [c.strip() for c in row.strip("|").split("|")]
        data.append([Paragraph(inline(c), style["cell"]) for c in cells])

    widths = [None] * len(columns)
    table = Table(data, colWidths=widths, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, RULE),
        ("LINEBELOW", (0, 1), (-1, -2), 0.25, colors.HexColor("#e6e6e4")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    return table


def build(markdown: str, style) -> list:
    flow = []
    lines = markdown.splitlines()
    index = 0

    while index < len(lines):
        line = lines[index]

        if line.startswith("```"):
            block = []
            index += 1
            while index < len(lines) and not lines[index].startswith("```"):
                block.append(lines[index])
                index += 1
            # Code blocks bypass inline(), so they need sanitising here too --
            # they are where most of the arrows live.
            code = Preformatted(to_ascii("\n".join(block)), style["code"])
            panel = Table([[code]], hAlign="LEFT")
            panel.setStyle(TableStyle([
                ("BACKGROUND", (0, 0), (-1, -1), PANEL),
                ("BOX", (0, 0), (-1, -1), 0.4, RULE),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]))
            flow += [panel, Spacer(1, 3 * mm)]

        elif line.startswith("|"):
            rows = []
            while index < len(lines) and lines[index].startswith("|"):
                rows.append(lines[index])
                index += 1
            index -= 1
            flow += [table_from(rows, style), Spacer(1, 3 * mm)]

        elif line.startswith("### "):
            flow.append(Paragraph(inline(line[4:]), style["h3"]))
        elif line.startswith("## "):
            flow.append(Paragraph(inline(line[3:]), style["h2"]))
        elif line.startswith("# "):
            flow.append(Paragraph(inline(line[2:]), style["title"]))
        elif line.strip() == "---":
            flow += [Spacer(1, 1 * mm),
                     HRFlowable(width="100%", thickness=0.5, color=RULE),
                     Spacer(1, 2 * mm)]

        elif line.startswith("- ") or re.match(r"^\d+\. ", line):
            items = []
            bullet = "bullet" if line.startswith("- ") else "1"
            while index < len(lines) and (
                lines[index].startswith("- ") or re.match(r"^\d+\. ", lines[index])
            ):
                text = re.sub(r"^(- |\d+\. )", "", lines[index])
                items.append(ListItem(Paragraph(inline(text), style["body"]),
                                      leftIndent=6 * mm))
                index += 1
            index -= 1
            flow += [ListFlowable(items, bulletType=bullet, start=None,
                                  leftIndent=4 * mm), Spacer(1, 1 * mm)]

        elif line.strip():
            paragraph = [line]
            while index + 1 < len(lines) and lines[index + 1].strip() and not (
                lines[index + 1].startswith(("#", "|", "```", "- "))
                or lines[index + 1].strip() == "---"
            ):
                index += 1
                paragraph.append(lines[index])
            flow.append(Paragraph(inline(" ".join(paragraph)), style["body"]))

        index += 1

    return flow


def footer(canvas, document):
    canvas.saveState()
    canvas.setFont("Helvetica", 7.5)
    canvas.setFillColor(QUIET)
    canvas.drawString(20 * mm, 12 * mm, "Project Amanda - presence wiring")
    canvas.drawRightString(A4[0] - 20 * mm, 12 * mm, f"{document.page}")
    canvas.restoreState()


def main() -> int:
    if not SOURCE.is_file():
        print(f"missing {SOURCE}")
        return 1

    document = SimpleDocTemplate(
        str(OUTPUT), pagesize=A4,
        leftMargin=20 * mm, rightMargin=20 * mm,
        topMargin=18 * mm, bottomMargin=20 * mm,
        title="Project Amanda - presence wiring",
        author="Project Amanda",
    )
    document.build(build(SOURCE.read_text(encoding="utf-8"), styles()),
                   onFirstPage=footer, onLaterPages=footer)
    print(f"wrote {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
