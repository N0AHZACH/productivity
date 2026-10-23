"""Low-level Word style and OOXML helpers."""

from typing import Any, Literal

from docx.document import Document as DocumentObject
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.section import Section
from docx.shared import Cm, Pt, RGBColor
from docx.table import _Cell, _Row
from docx.text.paragraph import Paragraph

from app.render.themes import Theme

BorderSpec = tuple[str, int]
Edge = Literal["top", "bottom", "left", "right"]


def hex_to_rgb(value: str) -> RGBColor:
    """Convert a six-character hex color to a docx RGBColor."""
    cleaned = value.strip().lstrip("#")
    if len(cleaned) != 6:
        raise ValueError(f"Expected a six-character hex color, got {value!r}")
    return RGBColor.from_string(cleaned.upper())


def _set_style_font(style: Any, font_name: str, size_pt: float | None = None) -> None:
    style.font.name = font_name
    if size_pt is not None:
        style.font.size = Pt(size_pt)
    r_pr = style.element.get_or_add_rPr()
    r_fonts = r_pr.get_or_add_rFonts()
    r_fonts.set(qn("w:ascii"), font_name)
    r_fonts.set(qn("w:hAnsi"), font_name)
    r_fonts.set(qn("w:eastAsia"), font_name)
    r_fonts.set(qn("w:cs"), font_name)


def configure_document_styles(document: DocumentObject, theme: Theme) -> None:
    """Configure the named styles used by the renderer."""
    normal = document.styles["Normal"]
    _set_style_font(normal, theme.font_body, theme.body_pt)
    normal.paragraph_format.line_spacing = 1.08
    normal.paragraph_format.space_after = Pt(8)
    normal.paragraph_format.widow_control = True

    heading_specs = {
        "Heading 1": (theme.h1_pt, 18, 8),
        "Heading 2": (theme.h2_pt, 12, 5),
        "Heading 3": (theme.h3_pt, 9, 4),
    }
    for name, (size, before, after) in heading_specs.items():
        style = document.styles[name]
        _set_style_font(style, theme.font_heading, size)
        style.font.bold = True
        style.font.color.rgb = hex_to_rgb(theme.accent_hex)
        style.paragraph_format.space_before = Pt(before)
        style.paragraph_format.space_after = Pt(after)
        style.paragraph_format.keep_with_next = True
        style.paragraph_format.line_spacing = 1.0
        style.paragraph_format.widow_control = True

    heading_four = document.styles["Heading 4"]
    _set_style_font(heading_four, theme.font_heading, theme.body_pt)
    heading_four.font.bold = True
    heading_four.font.italic = False
    heading_four.font.color.rgb = hex_to_rgb(theme.accent_hex)
    heading_four.paragraph_format.space_before = Pt(8)
    heading_four.paragraph_format.space_after = Pt(3)
    heading_four.paragraph_format.keep_with_next = True

    title = document.styles["Title"]
    _set_style_font(title, theme.font_heading, theme.h1_pt * 1.9)
    title.font.bold = True
    title.font.color.rgb = hex_to_rgb(theme.accent_hex)
    title.paragraph_format.space_after = Pt(8)
    title.paragraph_format.line_spacing = 1.0

    if "Subtitle" in [style.name for style in document.styles]:
        subtitle = document.styles["Subtitle"]
        _set_style_font(subtitle, theme.font_body, theme.body_pt + 3)
        subtitle.font.italic = False
        subtitle.font.color.rgb = hex_to_rgb("595959")
        subtitle.paragraph_format.space_after = Pt(22)

    if "Caption" in [style.name for style in document.styles]:
        caption = document.styles["Caption"]
        caption.font.italic = True
        caption.font.size = Pt(theme.body_pt - 1)
        caption.font.color.rgb = hex_to_rgb("595959")


def configure_page(section: Section) -> None:
    """Set A4 page size and consistent margins."""
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.2)
    section.bottom_margin = Cm(2.2)
    section.left_margin = Cm(2.2)
    section.right_margin = Cm(2.2)
    section.header_distance = Cm(1.1)
    section.footer_distance = Cm(1.0)


def set_cell_background(cell: _Cell, hex_color: str) -> None:
    """Fill a table cell with a solid color."""
    tc_pr = cell._tc.get_or_add_tcPr()
    shading = tc_pr.find(qn("w:shd"))
    if shading is None:
        shading = OxmlElement("w:shd")
        tc_pr.append(shading)
    shading.set(qn("w:fill"), hex_color.strip().lstrip("#").upper())
    shading.set(qn("w:val"), "clear")


def set_cell_borders(
    cell: _Cell,
    *,
    top: BorderSpec | None = None,
    bottom: BorderSpec | None = None,
    left: BorderSpec | None = None,
    right: BorderSpec | None = None,
) -> None:
    """Set individual cell borders. Each spec is (hex_color, size_eighths_pt)."""
    tc_pr = cell._tc.get_or_add_tcPr()
    borders = tc_pr.find(qn("w:tcBorders"))
    if borders is None:
        borders = OxmlElement("w:tcBorders")
        tc_pr.append(borders)
    specs: dict[str, BorderSpec | None] = {
        "top": top,
        "bottom": bottom,
        "left": left,
        "right": right,
    }
    for edge, spec in specs.items():
        existing = borders.find(qn(f"w:{edge}"))
        if existing is not None:
            borders.remove(existing)
        if spec is None:
            continue
        color, size = spec
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:val"), "single")
        element.set(qn("w:sz"), str(max(1, int(size))))
        element.set(qn("w:space"), "0")
        element.set(qn("w:color"), color.strip().lstrip("#").upper())
        borders.append(element)


def set_cell_margins(
    cell: _Cell, *, top_cm: float, bottom_cm: float, left_cm: float, right_cm: float
) -> None:
    """Set cell internal margins in centimeters."""
    tc_pr = cell._tc.get_or_add_tcPr()
    margins = tc_pr.find(qn("w:tcMar"))
    if margins is None:
        margins = OxmlElement("w:tcMar")
        tc_pr.append(margins)
    for edge, value in (
        ("top", top_cm),
        ("bottom", bottom_cm),
        ("left", left_cm),
        ("right", right_cm),
    ):
        element = OxmlElement(f"w:{edge}")
        element.set(qn("w:w"), str(int(value * 567)))
        element.set(qn("w:type"), "dxa")
        margins.append(element)


def _add_field(paragraph: Paragraph, instruction: str, placeholder: str = "1") -> None:
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    begin.set(qn("w:dirty"), "true")
    instr = OxmlElement("w:instrText")
    instr.set(qn("xml:space"), "preserve")
    instr.text = f" {instruction} "
    separate = OxmlElement("w:fldChar")
    separate.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = placeholder
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for element in (begin, instr, separate, text, end):
        run._r.append(element)


def add_page_number_field(paragraph: Paragraph) -> None:
    """Append a PAGE field to a paragraph."""
    _add_field(paragraph, "PAGE")


def add_page_count_field(paragraph: Paragraph) -> None:
    """Append a NUMPAGES field to a paragraph."""
    _add_field(paragraph, "NUMPAGES")


def add_toc_field(paragraph: Paragraph) -> None:
    """Add a Word table-of-contents field covering headings 1 and 2."""
    _add_field(paragraph, r'TOC \o "1-3" \h \z \u', "Updating contents…")


def add_page_ref_field(paragraph: Paragraph, bookmark: str, placeholder: str = "—") -> None:
    """Append a PAGEREF field that resolves to a bookmarked heading's page."""
    _add_field(paragraph, f"PAGEREF {bookmark} \\h", placeholder)


def add_bookmark(paragraph: Paragraph, name: str, bookmark_id: int) -> None:
    """Wrap a heading with a bookmark so TOC page references can update."""
    start = OxmlElement("w:bookmarkStart")
    start.set(qn("w:id"), str(bookmark_id))
    start.set(qn("w:name"), name)
    end = OxmlElement("w:bookmarkEnd")
    end.set(qn("w:id"), str(bookmark_id))
    paragraph._p.insert(0, start)
    paragraph._p.append(end)


def repeat_table_header(row: _Row) -> None:
    """Mark a table row as a repeating header."""
    tr_pr = row._tr.get_or_add_trPr()
    header = OxmlElement("w:tblHeader")
    header.set(qn("w:val"), "true")
    tr_pr.append(header)


def set_paragraph_bottom_border(
    paragraph: Paragraph, color: str, *, size: int = 12, space: int = 4
) -> None:
    """Add a horizontal rule under a paragraph."""
    p_pr = paragraph._p.get_or_add_pPr()
    borders = p_pr.find(qn("w:pBdr"))
    if borders is None:
        borders = OxmlElement("w:pBdr")
        p_pr.append(borders)
    bottom = OxmlElement("w:bottom")
    bottom.set(qn("w:val"), "single")
    bottom.set(qn("w:sz"), str(size))
    bottom.set(qn("w:space"), str(space))
    bottom.set(qn("w:color"), color.strip().lstrip("#").upper())
    borders.append(bottom)


def enable_field_updates(document: DocumentObject) -> None:
    """Ask Word to refresh fields such as TOC and page numbers on open."""
    settings = document.settings.element
    update = settings.find(qn("w:updateFields"))
    if update is None:
        update = OxmlElement("w:updateFields")
        settings.append(update)
    update.set(qn("w:val"), "true")


def format_run(
    paragraph: Paragraph,
    text: str,
    *,
    bold: bool = False,
    italic: bool = False,
    color: str | None = None,
    size_pt: float | None = None,
    font: str | None = None,
) -> Any:
    """Add a directly formatted run to a paragraph and return it."""
    run = paragraph.add_run(text)
    run.bold = bold
    run.italic = italic
    if color:
        run.font.color.rgb = hex_to_rgb(color)
    if size_pt:
        run.font.size = Pt(size_pt)
    if font:
        run.font.name = font
    return run


def align(paragraph: Paragraph, alignment: WD_ALIGN_PARAGRAPH) -> None:
    """Small convenience wrapper used for cover and page furniture."""
    paragraph.alignment = alignment
