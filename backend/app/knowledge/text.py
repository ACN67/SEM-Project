"""Parse original document text with stable physical locators."""
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
import re

from docx import Document
from ..contracts import models as m
from .chunking import split_text
from .storage import ingest_error

RULE_VERSION = "w3-text-1"

@dataclass(frozen=True)
class ParsedText:
    evidences: list[m.Evidence]
    diagnostics: list[str]

def _docx_parts(path):
    with path.open("rb") as stream:
        document = Document(stream)
        headings = []
        for number, paragraph in enumerate(document.paragraphs, 1):
            style = paragraph.style.name
            level = re.fullmatch(r"Heading ([1-9])", style)
            if level and paragraph.text.strip():
                depth = int(level.group(1))
                headings = [(n,t) for n,t in headings if n < depth]
                headings.append((depth,paragraph.text))
            if paragraph.text.strip():
                yield m.Word(kind="docx", paragraph=number, section_path=[t for _,t in headings]), paragraph.text

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
            pages[number] = "\n".join(cell.text for cell in page.get_text_cells())
        if set(pages) != set(range(1, document.page_count + 1)):
            raise ValueError("Incomplete PDF page extraction")
        for number, excerpt in sorted(pages.items()):
            if excerpt.strip():
                yield m.Pdf(kind="pdf", page=number), excerpt
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
        for locator, excerpt in parts:
            for index, chunk in enumerate(split_text(excerpt)):
                key = sha256(f"{record.document_version_id}\0{locator.model_dump_json()}\0{index}\0{RULE_VERSION}".encode()).hexdigest()
                evidences.append(m.Evidence(**record.model_dump(include=set(m.Metadata.model_fields)),
                    evidence_id=key, chunk_id=key, document_id=record.document_id,
                    document_version_id=record.document_version_id, locator=locator, excerpt=chunk))
        if not evidences:
            raise ValueError("document has no readable text")
    except Exception as exc:
        raise ingest_error("DOCUMENT_PARSE_FAILED", f"{original_filename}: {exc}") from exc
    return ParsedText(evidences, diagnostics)
