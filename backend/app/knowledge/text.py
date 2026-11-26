"""Parse original document text with stable physical locators."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn
from ..contracts import models as m
from .chunking import split_text
from .storage import ingest_error

RULE_VERSION = "w3-text-2"

@dataclass(frozen=True)
class ParsedText:
    evidences: list[m.Evidence]
    diagnostics: list[str]

def _heading_depth(paragraph):
    # A direct outline level overrides the inherited paragraph style.
    properties = [paragraph._p.pPr]
    style = paragraph.style
    visited = set()
    while style is not None and style.style_id not in visited:
        visited.add(style.style_id)
        properties.append(style.element.pPr)
        style = style.base_style
    for props in properties:
        if props is not None:
            level = props.find(qn("w:outlineLvl"))
            if level is not None:
                value = int(level.get(qn("w:val")))
                return value + 1 if 0 <= value <= 8 else None
    return None

def _page_paragraphs(cells):
    """Group adjacent text lines using their geometric separation."""
    paragraphs = []
    lines = []
    previous = None
    for cell in cells:
        if not cell.text.strip():
            continue
        box = cell.rect.to_bounding_box()
        if previous is not None:
            height = max(abs(previous.t-previous.b),abs(box.t-box.b),1)
            gap = (box.t-previous.b if box.coord_origin.value == "TOPLEFT"
                   else previous.b-box.t)
            # A large vertical gap or a jump back up the page starts a new block.
            if gap > height * 1.4 or gap < -height * 2:
                paragraphs.append("\n".join(lines))
                lines = []
        lines.append(cell.text)
        previous = box
    if lines:
        paragraphs.append("\n".join(lines))
    return paragraphs

def _docx_parts(path):
    with path.open("rb") as stream:
        document = Document(stream)
        headings = []
        for number, paragraph in enumerate(document.paragraphs, 1):
            depth = _heading_depth(paragraph)
            if depth and paragraph.text.strip():
                headings = [(n,t) for n,t in headings if n < depth]
                headings.append((depth,paragraph.text))
            if paragraph.text.strip():
                yield m.Word(kind="docx", paragraph=number, section_path=[t for _,t in headings]), 0, paragraph.text

def _pdf_parts(path, diagnostics):
    from docling.backend.docling_parse_backend import ThreadedDoclingParseDocumentBackend
    from docling.datamodel.document import InputDocument
    from docling.datamodel.base_models import InputFormat
    from pypdf import PdfReader

    with path.open("rb") as stream:
        if PdfReader(stream).is_encrypted:
            raise ValueError("encrypted PDF")
    document = InputDocument(path, InputFormat.PDF, ThreadedDoclingParseDocumentBackend)
    backend = document._backend
    try:
        if not document.valid or backend is None:
            raise ValueError("invalid PDF")
        pages = {}
        for page in backend.iter_pages():
            number = page.page_no
            if number in pages or not page.is_valid():
                raise ValueError(f"PDF page {number} failed to parse")
            pages[number] = _page_paragraphs(page.get_text_cells())
        if set(pages) != set(range(1, document.page_count + 1)):
            raise ValueError("Incomplete PDF page extraction")
        for number, paragraphs in sorted(pages.items()):
            if paragraphs:
                for part_index, excerpt in enumerate(paragraphs):
                    yield m.Pdf(kind="pdf", page=number), part_index, excerpt
            else:
                diagnostics.append(f"Page {number}: no readable text")
    finally:
        if backend is not None:
            backend.unload()

def parse_text(path: Path, original_filename: str, record: m.DocumentRecord) -> ParsedText:
    diagnostics = []
    evidences = []
    try:
        suffix = Path(original_filename).suffix.lower()
        if suffix == ".docx":
            parts = _docx_parts(path)
        elif suffix == ".pdf":
            parts = _pdf_parts(path, diagnostics)
        else:
            raise ValueError("unsupported text format")
        for locator, part_index, excerpt in parts:
            for index, chunk in enumerate(split_text(excerpt)):
                key = sha256(f"{record.document_version_id}\0{locator.model_dump_json()}\0{part_index}\0{index}\0{RULE_VERSION}".encode()).hexdigest()
                evidences.append(m.Evidence(**record.model_dump(include=set(m.Metadata.model_fields)),
                    evidence_id=key, chunk_id=key, document_id=record.document_id,
                    document_version_id=record.document_version_id, locator=locator, excerpt=chunk))
        if not evidences:
            raise ValueError("document has no readable text")
    except Exception as exc:
        raise ingest_error("DOCUMENT_PARSE_FAILED", f"{original_filename}: {exc}") from exc
    return ParsedText(evidences, diagnostics)
