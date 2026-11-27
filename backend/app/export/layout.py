"""Versioned Word layout and field helpers."""
from pathlib import Path
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH

ROOT = Path(__file__).resolve().parents[3] / "templates" / "docx"

def field(paragraph, instruction):
    run = paragraph.add_run()
    for kind in ("begin", "instruction", "separate", "end"):
        if kind == "instruction":
            node = OxmlElement("w:instrText")
            node.set(qn("xml:space"), "preserve")
            node.text = instruction
        else:
            node = OxmlElement("w:fldChar")
            node.set(qn("w:fldCharType"), kind)
        run._r.append(node)

def make_base(path):
    document = Document()
    section = document.sections[0]
    section.page_width, section.page_height = Cm(21), Cm(29.7)
    section.top_margin = section.bottom_margin = section.left_margin = section.right_margin = Cm(2.5)
    for name, size, font, color, bold in (
        ("Normal", 12, "SimSun", "263548", False),
        ("Title", 26, "Microsoft YaHei", "173B59", True),
        ("Heading 1", 18, "Microsoft YaHei", "173B59", True),
        ("Heading 2", 14, "Microsoft YaHei", "173B59", True),
        ("Report Cover Title", 28, "Microsoft YaHei", "173B59", True),
        ("Report Cover Period", 16, "Microsoft YaHei", "42657F", False),
        ("Report Cover Details", 11, "Microsoft YaHei", "526579", False),
        ("Report Contents", 22, "Microsoft YaHei", "173B59", True),
        ("Report Table Text", 10.5, "SimSun", "263548", False),
        ("Report Table Header", 10.5, "Microsoft YaHei", "173B59", True),
    ):
        style = document.styles[name] if name in document.styles else document.styles.add_style(name, WD_STYLE_TYPE.PARAGRAPH)
        style.font.name = font
        style.font.size = Pt(size)
        style.font.bold = bold
        style.font.color.rgb = RGBColor.from_string(color)
        style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), font)
        style.paragraph_format.space_after = Pt(8)
    normal = document.styles["Normal"].paragraph_format
    normal.line_spacing = 1.5
    normal.first_line_indent = Pt(24)
    for name in ("Heading 1", "Heading 2", "Report Contents"):
        fmt = document.styles[name].paragraph_format
        fmt.space_before = Pt(18)
        fmt.space_after = Pt(10)
        fmt.keep_with_next = True
        fmt.first_line_indent = Pt(0)
    cover = document.styles["Report Cover Title"].paragraph_format
    cover.space_before = Pt(110)
    cover.space_after = Pt(20)
    cover.keep_with_next = True
    for name in ("Report Cover Period", "Report Cover Details", "Report Table Text", "Report Table Header"):
        fmt = document.styles[name].paragraph_format
        fmt.first_line_indent = Pt(0)
        fmt.line_spacing = 1.15
        fmt.space_after = Pt(3)
        fmt.space_before = Pt(3)
    footer = section.footer.paragraphs[0]
    footer.alignment = WD_ALIGN_PARAGRAPH.CENTER
    footer.paragraph_format.first_line_indent = Pt(0)
    field(footer, " PAGE ")
    settings = document.settings.element
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    settings.append(update)
    document.save(path)

def load_layout(version, *, root=None):
    if version != "1":
        raise ValueError(f"Unknown Word layout: {version}")
    from .pdf import export_error
    try:
        document = Document((Path(root) if root is not None else ROOT) / version / "base.docx")
        required = {"Normal", "Heading 1", "Heading 2", "Report Contents",
            "Report Cover Title", "Report Cover Period", "Report Cover Details",
            "Report Table Header", "Report Table Text"}
        for name in required:
            if name not in document.styles or document.styles[name].type != WD_STYLE_TYPE.PARAGRAPH:
                raise ValueError("Missing paragraph style: " + name)
        if "Table Grid" not in document.styles or document.styles["Table Grid"].type != WD_STYLE_TYPE.TABLE:
            raise ValueError("Missing table style: Table Grid")
        return document
    except Exception as exc:
        raise export_error("layout_invalid") from exc

if __name__ == "__main__":
    make_base(ROOT / "1" / "base.docx")
