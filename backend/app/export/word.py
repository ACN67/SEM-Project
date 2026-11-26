"""Render an immutable report snapshot to a basic DOCX document."""
from io import BytesIO

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from ..contracts import models as m
from ..contracts.validation import check_snapshot
from ..contracts.services import ServiceError
from .layout import field, load_layout

REPORT_NAMES = {
    "industry_analysis": "产业分析报告",
    "enterprise_comparison": "企业对标报告",
    "industry_prosperity": "产业景气报告",
    "investment_attraction": "招商报告",
    "overseas_assessment": "出海研判报告",
}

def _error(code, message):
    return ServiceError(m.ErrorInfo(code=code, message=message, retryable=False, details={}))

def render_docx(snapshot: m.ReportDocument) -> bytes:
    try:
        snapshot = m.ReportDocument.model_validate(snapshot.model_dump(by_alias=True))
        check_snapshot(snapshot)
    except (ValueError, AttributeError) as exc:
        raise _error("INVALID_SNAPSHOT", str(exc)) from exc
    if not snapshot.exportable:
        raise _error("REPORT_NOT_EXPORTABLE", "Snapshot cannot be exported")
    if any(block.type == "chart_ref" for chapter in snapshot.chapters for block in chapter.blocks):
        raise _error("UNSUPPORTED_BLOCK", "Chart references require the K3 renderer")
    try:
        document = load_layout(snapshot.layout_version)
    except ValueError as exc:
        raise _error("UNKNOWN_LAYOUT", str(exc)) from exc
    spec = snapshot.spec
    document.add_paragraph(REPORT_NAMES[spec.report_type], "Title")
    for label, value in (("产业", spec.industry), ("地区", spec.region),
                         ("期间", f"{spec.period.from_} – {spec.period.to}"),
                         ("版本", snapshot.version_id), ("日期", snapshot.created_at.date().isoformat())):
        document.add_paragraph(f"{label}：{value}")
    document.add_page_break()
    document.add_paragraph("目录", "Title")
    field(document.add_paragraph(), ' TOC \\o "1-2" \\h \\z \\u ')
    document.add_page_break()
    chapters = {chapter.chapter_id: chapter for chapter in snapshot.chapters}
    for task in sorted(snapshot.outline.chapters, key=lambda item: item.order):
        chapter = chapters.get(task.chapter_id)
        if chapter is None:
            continue
        document.add_heading(chapter.title, level=1)
        for block in chapter.blocks:
            if isinstance(block, m.Paragraph):
                document.add_paragraph(block.text)
            elif isinstance(block, m.Table):
                table = document.add_table(rows=1, cols=len(block.columns))
                table.style = "Table Grid"
                for cell, title in zip(table.rows[0].cells, block.columns, strict=True):
                    cell.text = title
                marker = OxmlElement("w:tblHeader")
                marker.set(qn("w:val"), "true")
                table.rows[0]._tr.get_or_add_trPr().append(marker)
                for row in block.rows:
                    for cell, fact in zip(table.add_row().cells, row, strict=True):
                        cell.text = fact.text
    content = BytesIO()
    document.save(content)
    return content.getvalue()
