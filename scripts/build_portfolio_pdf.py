"""Build the project-manager portfolio PDF from docs/PROJECT_PORTFOLIO.md."""

from __future__ import annotations

import html
import re
from collections.abc import Iterable
from pathlib import Path

from reportlab.graphics.shapes import Drawing, Line, Rect, String
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    PageBreak,
    PageTemplate,
    Paragraph,
    Preformatted,
    Spacer,
    Table,
    TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs" / "PROJECT_PORTFOLIO.md"
OUTPUT = ROOT / "docs" / "PROJECT_PORTFOLIO.pdf"

NAVY = colors.HexColor("#17324D")
BLUE = colors.HexColor("#2F6F9F")
PALE_BLUE = colors.HexColor("#EAF3F9")
INK = colors.HexColor("#17212B")
MUTED = colors.HexColor("#5B6875")
RULE = colors.HexColor("#C7D1D9")
PALE = colors.HexColor("#F5F8FA")


def register_fonts() -> str:
    """Use a Unicode-capable font when available, otherwise use Helvetica."""
    candidates = [
        ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "DejaVuSans"),
        ("/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf", "LiberationSans"),
    ]
    for path, name in candidates:
        if Path(path).is_file():
            pdfmetrics.registerFont(TTFont(name, path))
            bold_path = path.replace(".ttf", "-Bold.ttf")
            if Path(bold_path).is_file():
                pdfmetrics.registerFont(TTFont(f"{name}-Bold", bold_path))
            return name
    return "Helvetica"


FONT = register_fonts()
BOLD_FONT = f"{FONT}-Bold" if FONT != "Helvetica" else "Helvetica-Bold"


def inline(text: str) -> str:
    """Convert a small safe subset of Markdown inline syntax to reportlab markup."""
    value = html.escape(text, quote=False)
    value = re.sub(r"\[([^]]+)\]\(([^)]+)\)", r"\1", value)
    value = re.sub(r"`([^`]+)`", r"<font name='Courier'>\1</font>", value)
    value = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", value)
    value = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", value)
    return value


def styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "body": ParagraphStyle(
            "PortfolioBody",
            parent=base["BodyText"],
            fontName=FONT,
            fontSize=9.2,
            leading=13.2,
            textColor=INK,
            spaceAfter=7,
        ),
        "small": ParagraphStyle(
            "PortfolioSmall",
            parent=base["BodyText"],
            fontName=FONT,
            fontSize=8,
            leading=10.5,
            textColor=MUTED,
        ),
        "h1": ParagraphStyle(
            "PortfolioH1",
            parent=base["Heading1"],
            fontName=BOLD_FONT,
            fontSize=18,
            leading=22,
            textColor=NAVY,
            spaceBefore=16,
            spaceAfter=8,
            keepWithNext=True,
        ),
        "h2": ParagraphStyle(
            "PortfolioH2",
            parent=base["Heading2"],
            fontName=BOLD_FONT,
            fontSize=13,
            leading=17,
            textColor=BLUE,
            spaceBefore=13,
            spaceAfter=6,
            keepWithNext=True,
        ),
        "h3": ParagraphStyle(
            "PortfolioH3",
            parent=base["Heading3"],
            fontName=BOLD_FONT,
            fontSize=10.5,
            leading=14,
            textColor=NAVY,
            spaceBefore=9,
            spaceAfter=4,
            keepWithNext=True,
        ),
        "bullet": ParagraphStyle(
            "PortfolioBullet",
            parent=base["BodyText"],
            fontName=FONT,
            fontSize=9,
            leading=12.5,
            leftIndent=13,
            firstLineIndent=-8,
            textColor=INK,
            spaceAfter=3,
        ),
        "quote": ParagraphStyle(
            "PortfolioQuote",
            parent=base["BodyText"],
            fontName=FONT,
            fontSize=10,
            leading=14,
            leftIndent=16,
            rightIndent=16,
            textColor=NAVY,
            backColor=PALE_BLUE,
            borderColor=BLUE,
            borderWidth=1,
            borderPadding=9,
            spaceBefore=5,
            spaceAfter=9,
        ),
        "code": ParagraphStyle(
            "PortfolioCode",
            fontName="Courier",
            fontSize=7.2,
            leading=9.2,
            textColor=INK,
            backColor=PALE,
            borderColor=RULE,
            borderWidth=0.5,
            borderPadding=7,
            spaceBefore=4,
            spaceAfter=8,
        ),
    }


def table_from_rows(rows: list[list[str]], style: dict[str, ParagraphStyle]) -> Table:
    """Create a styled table from Markdown table rows."""
    if not rows:
        return Table([[]])
    usable = [[Paragraph(inline(cell.strip()), style["body"]) for cell in row] for row in rows]
    widths = [max(3.0 * cm, 16.0 * cm / len(rows[0]))] * len(rows[0])
    table = Table(usable, colWidths=widths, repeatRows=1, hAlign="LEFT")
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), NAVY),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("FONTNAME", (0, 0), (-1, 0), BOLD_FONT),
                ("GRID", (0, 0), (-1, -1), 0.35, RULE),
                ("BACKGROUND", (0, 1), (-1, -1), colors.white),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, PALE]),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 6),
                ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                ("TOPPADDING", (0, 0), (-1, -1), 5),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ]
        )
    )
    return table


def architecture_figure() -> Drawing:
    """Draw a compact architecture figure as a vector graphic."""
    drawing = Drawing(500, 205)
    boxes = [
        (15, 145, 100, 35, "User / UI", PALE_BLUE),
        (140, 145, 105, 35, "FastAPI", colors.HexColor("#E8F1FA")),
        (270, 145, 115, 35, "Orchestrator", colors.HexColor("#E7F4ED")),
        (410, 145, 75, 35, "Trace", colors.HexColor("#FFF5DD")),
        (60, 70, 105, 35, "Analyzer + Planner", colors.HexColor("#EAF3F9")),
        (195, 70, 105, 35, "Executor + Tools", colors.HexColor("#E7F4ED")),
        (330, 70, 105, 35, "Review + Render", colors.HexColor("#F5EAF8")),
        (195, 10, 105, 35, "Readable .docx", colors.HexColor("#FFF5DD")),
    ]
    for x, y, width, height, label, fill in boxes:
        drawing.add(Rect(x, y, width, height, rx=5, ry=5, fillColor=fill, strokeColor=BLUE))
        drawing.add(
            String(
                x + width / 2,
                y + 13,
                label,
                fontName=BOLD_FONT,
                fontSize=8,
                fillColor=NAVY,
                textAnchor="middle",
            )
        )
    arrows = [
        (115, 162, 140, 162),
        (245, 162, 270, 162),
        (385, 162, 410, 162),
        (190, 145, 115, 105),
        (300, 145, 247, 105),
        (355, 145, 382, 105),
        (247, 70, 247, 45),
    ]
    for x1, y1, x2, y2 in arrows:
        drawing.add(Line(x1, y1, x2, y2, strokeColor=BLUE, strokeWidth=1.2))
    return drawing


def lifecycle_figure() -> Drawing:
    """Draw the agent lifecycle as a vector pipeline."""
    drawing = Drawing(500, 105)
    labels = ["Guardrails", "Analysis", "Plan", "Execute", "Reflect", "Render"]
    fills = [
        PALE_BLUE,
        colors.HexColor("#E8F1FA"),
        colors.HexColor("#EAF3F9"),
        colors.HexColor("#E7F4ED"),
        colors.HexColor("#F5EAF8"),
        colors.HexColor("#FFF5DD"),
    ]
    start = 8
    width = 75
    gap = 9
    for index, label in enumerate(labels):
        x = start + index * (width + gap)
        drawing.add(Rect(x, 42, width, 32, rx=5, ry=5, fillColor=fills[index], strokeColor=BLUE))
        drawing.add(
            String(
                x + width / 2,
                54,
                label,
                fontName=BOLD_FONT,
                fontSize=7.4,
                fillColor=NAVY,
                textAnchor="middle",
            )
        )
        if index < len(labels) - 1:
            drawing.add(Line(x + width, 58, x + width + gap, 58, strokeColor=BLUE, strokeWidth=1.2))
    drawing.add(
        String(
            250,
            19,
            "Typed contracts, deterministic tools, bounded recovery",
            fontName=FONT,
            fontSize=8,
            fillColor=MUTED,
            textAnchor="middle",
        )
    )
    return drawing


def parse_markdown(lines: Iterable[str], style: dict[str, ParagraphStyle]) -> list[object]:
    """Parse the project Markdown into a practical subset of reportlab flowables."""
    flow: list[object] = []
    paragraph: list[str] = []
    list_items: list[str] = []
    code_lines: list[str] = []
    in_code = False
    code_is_mermaid = False
    table_rows: list[list[str]] = []
    lines_list = list(lines)

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            flow.append(
                Paragraph(inline(" ".join(part.strip() for part in paragraph)), style["body"])
            )
            paragraph = []

    def flush_list() -> None:
        nonlocal list_items
        for item in list_items:
            flow.append(Paragraph(f"• {inline(item)}", style["bullet"]))
        list_items = []

    def flush_table() -> None:
        nonlocal table_rows
        if table_rows:
            flow.append(table_from_rows(table_rows, style))
            flow.append(Spacer(1, 0.16 * cm))
            table_rows = []

    def flush_code() -> None:
        nonlocal code_lines
        if code_lines and not code_is_mermaid:
            flow.append(Preformatted("\n".join(code_lines), style["code"]))
        code_lines = []

    for raw in lines_list:
        line = raw.rstrip("\n")
        if line.startswith("```"):
            if in_code:
                flush_code()
                in_code = False
                code_is_mermaid = False
            else:
                flush_paragraph()
                flush_list()
                flush_table()
                in_code = True
                code_is_mermaid = line.lower().startswith("```mermaid")
            continue
        if in_code:
            code_lines.append(line)
            continue
        if not line.strip():
            flush_paragraph()
            flush_list()
            flush_table()
            flow.append(Spacer(1, 0.07 * cm))
            continue
        if line.strip() == "---":
            flush_paragraph()
            flush_list()
            flush_table()
            flow.append(
                HRFlowable(width="100%", thickness=0.7, color=RULE, spaceBefore=5, spaceAfter=8)
            )
            continue
        heading = re.match(r"^(#{1,3})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            flush_list()
            flush_table()
            level = len(heading.group(1))
            if level == 1:
                flow.append(Paragraph(inline(heading.group(2)), style["h1"]))
            elif level == 2:
                flow.append(Paragraph(inline(heading.group(2)), style["h2"]))
            else:
                flow.append(Paragraph(inline(heading.group(2)), style["h3"]))
            continue
        if line.startswith("> "):
            flush_paragraph()
            flush_list()
            flush_table()
            flow.append(Paragraph(inline(line[2:]), style["quote"]))
            continue
        if line.startswith("|") and line.endswith("|"):
            flush_paragraph()
            flush_list()
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            if all(re.fullmatch(r":?-+:?", cell) for cell in cells):
                continue
            table_rows.append(cells)
            continue
        bullet = re.match(r"^\s*(?:[-*]|\d+\.)\s+(.+)$", line)
        if bullet:
            flush_paragraph()
            flush_table()
            list_items.append(bullet.group(1))
            continue
        if list_items:
            flush_list()
        if table_rows:
            flush_table()
        paragraph.append(line)

    flush_paragraph()
    flush_list()
    flush_table()
    flush_code()
    return flow


class PortfolioDocTemplate(BaseDocTemplate):
    """Document template with a restrained portfolio-style header/footer."""

    def __init__(self, filename: str, **kwargs: object) -> None:
        super().__init__(filename, **kwargs)
        frame = Frame(
            self.leftMargin,
            self.bottomMargin,
            self.width,
            self.height,
            id="normal",
            leftPadding=0,
            rightPadding=0,
            topPadding=0,
            bottomPadding=0,
        )
        self.addPageTemplates(
            [PageTemplate(id="portfolio", frames=[frame], onPage=self._draw_page)]
        )

    def _draw_page(self, canvas: object, doc: object) -> None:
        canvas.saveState()
        canvas.setStrokeColor(RULE)
        canvas.setLineWidth(0.5)
        canvas.line(doc.leftMargin, A4[1] - 1.35 * cm, A4[0] - doc.rightMargin, A4[1] - 1.35 * cm)
        canvas.setFont(FONT, 7.5)
        canvas.setFillColor(MUTED)
        canvas.drawString(doc.leftMargin, 0.85 * cm, "Productivity | Autonomous Document Agent")
        canvas.drawRightString(A4[0] - doc.rightMargin, 0.85 * cm, f"Page {doc.page}")
        canvas.restoreState()


def build_pdf() -> Path:
    """Build the PDF artifact and return its path."""
    content = SOURCE.read_text(encoding="utf-8")
    lines = content.splitlines()
    style = styles()
    document = PortfolioDocTemplate(
        str(OUTPUT),
        pagesize=A4,
        leftMargin=1.8 * cm,
        rightMargin=1.8 * cm,
        topMargin=1.75 * cm,
        bottomMargin=1.45 * cm,
        title="Productivity — Autonomous Business-Document Agent",
        author="Noah Zach",
        subject="Project-manager portfolio case study",
    )

    story: list[object] = []
    story.extend(
        [
            Spacer(1, 1.1 * cm),
            Paragraph(
                "PRODUCTIVITY",
                ParagraphStyle(
                    "CoverKicker",
                    fontName=BOLD_FONT,
                    fontSize=10,
                    textColor=BLUE,
                    alignment=TA_CENTER,
                    tracking=2,
                ),
            ),
            Spacer(1, 0.35 * cm),
            Paragraph(
                "Autonomous Business-Document Agent",
                ParagraphStyle(
                    "CoverTitle",
                    fontName=BOLD_FONT,
                    fontSize=26,
                    leading=31,
                    textColor=NAVY,
                    alignment=TA_CENTER,
                ),
            ),
            Spacer(1, 0.35 * cm),
            Paragraph(
                "Project-manager portfolio case study",
                ParagraphStyle(
                    "CoverSub",
                    fontName=FONT,
                    fontSize=13,
                    leading=17,
                    textColor=MUTED,
                    alignment=TA_CENTER,
                ),
            ),
            Spacer(1, 0.9 * cm),
            architecture_figure(),
            Spacer(1, 0.7 * cm),
            Paragraph(
                "A natural-language request in. A planned, reviewed, traceable Word "
                "deliverable out.",
                ParagraphStyle(
                    "CoverClaim",
                    fontName=FONT,
                    fontSize=12,
                    leading=17,
                    textColor=INK,
                    alignment=TA_CENTER,
                ),
            ),
            Spacer(1, 1.2 * cm),
            Table(
                [
                    [
                        Paragraph("STATUS", style["small"]),
                        Paragraph(
                            "Demonstrable local MVP / portfolio-ready prototype", style["body"]
                        ),
                    ],
                    [
                        Paragraph("DELIVERY", style["small"]),
                        Paragraph(
                            "FastAPI + browser demo + CLI + Docker + .docx output", style["body"]
                        ),
                    ],
                    [
                        Paragraph("MODEL", style["small"]),
                        Paragraph(
                            "Provider-neutral adapter with Gemini, Groq, Ollama, and "
                            "OpenRouter support",
                            style["body"],
                        ),
                    ],
                    [
                        Paragraph("QUALITY", style["small"]),
                        Paragraph(
                            "Offline test suite, semantic plan validation, reflection, "
                            "retries, and deterministic rendering",
                            style["body"],
                        ),
                    ],
                ],
                colWidths=[2.4 * cm, 13.5 * cm],
                style=TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (0, -1), PALE_BLUE),
                        ("BOX", (0, 0), (-1, -1), 0.6, RULE),
                        ("INNERGRID", (0, 0), (-1, -1), 0.3, RULE),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 7),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 7),
                        ("TOPPADDING", (0, 0), (-1, -1), 6),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
                    ]
                ),
            ),
            PageBreak(),
            Paragraph("Contents", style["h1"]),
            Paragraph(
                "The report is organized as a delivery narrative: outcome, scope, "
                "architecture, workflow, quality, operations, and roadmap.",
                style["body"],
            ),
            lifecycle_figure(),
            Spacer(1, 0.25 * cm),
        ]
    )

    headings = [line[3:].strip() for line in lines if line.startswith("## ")]
    contents_rows = [
        [Paragraph(f"{index:02d}", style["small"]), Paragraph(inline(title), style["body"])]
        for index, title in enumerate(headings, 1)
    ]
    contents_table = Table(contents_rows, colWidths=[1.0 * cm, 15.0 * cm])
    contents_table.setStyle(
        TableStyle(
            [
                ("LINEBELOW", (0, 0), (-1, -1), 0.3, RULE),
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 4),
                ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
            ]
        )
    )
    story.append(contents_table)
    story.append(PageBreak())
    story.extend(parse_markdown(lines, style))
    document.build(story)
    return OUTPUT


if __name__ == "__main__":
    print(build_pdf())
