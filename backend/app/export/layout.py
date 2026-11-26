"""Versioned Word layout and field helpers."""
from pathlib import Path
from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt

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
    for name, size in (("Normal", 12), ("Title", 24), ("Heading 1", 18), ("Heading 2", 14)):
        style = document.styles[name]
        style.font.name = "SimSun"
        style.font.size = Pt(size)
        style.element.get_or_add_rPr().rFonts.set(qn("w:eastAsia"), "SimSun")
    footer = section.footer.paragraphs[0]
    footer.alignment = 1
    field(footer, " PAGE ")
    settings = document.settings.element
    update = OxmlElement("w:updateFields")
    update.set(qn("w:val"), "true")
    settings.append(update)
    document.save(path)

def load_layout(version):
    if version != "1":
        raise ValueError(f"Unknown Word layout: {version}")
    return Document(ROOT / version / "base.docx")

if __name__ == "__main__":
    make_base(ROOT / "1" / "base.docx")
