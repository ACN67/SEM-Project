"""Render an immutable report snapshot to a basic DOCX document."""
from io import BytesIO
import re
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from ..contracts import models as m
from ..contracts.validation import check_snapshot
from ..contracts.services import ServiceError
from .layout import field, load_layout
from .assets import AssetResolver, resolve_chart
from .citations import Citations

REPORT_NAMES = {
    "industry_analysis": "产业分析报告",
    "enterprise_comparison": "企业对标报告",
    "industry_prosperity": "产业景气报告",
    "investment_attraction": "招商报告",
    "overseas_assessment": "出海研判报告",
}

def _error(code, message):
    return ServiceError(m.ErrorInfo(code=code, message=message, retryable=False, details={}))

def _append_citation(paragraph, markers):
    if markers:
        run = paragraph.add_run(markers)
        run.font.superscript = True
        run.font.color.rgb = RGBColor.from_string("42657F")

def render_docx(snapshot: m.ReportDocument, *, asset_resolver: AssetResolver | None = None) -> bytes:
    try:
        snapshot = m.ReportDocument.model_validate(snapshot.model_dump(by_alias=True))
        check_snapshot(snapshot)
    except (ValueError, AttributeError) as exc:
        raise _error("INVALID_SNAPSHOT", str(exc)) from exc
    if not snapshot.exportable:
        raise _error("REPORT_NOT_EXPORTABLE", "Snapshot cannot be exported")
    try:
        citations = Citations(snapshot)
    except (ValueError, KeyError) as exc:
        raise _error("EXPORT_FAILED", str(exc)) from exc
    charts = {chart.chart_id: chart for chart in snapshot.charts}
    assets = {}
    for chapter in snapshot.chapters:
        for block in chapter.blocks:
            if isinstance(block, m.ChartRef):
                chart = charts[block.chart_id]
                if chart.chapter_id != chapter.chapter_id or chart.block_id != block.block_id:
                    raise ServiceError(m.ErrorInfo(code="EXPORT_FAILED", message="Chart block mismatch",
                        retryable=False, details={"chart_id":chart.chart_id,"asset_id":chart.asset_id}))
                assets[chart.chart_id] = resolve_chart(snapshot, chart, asset_resolver)
    try:
        document = load_layout(snapshot.layout_version)
    except ValueError as exc:
        raise _error("UNKNOWN_LAYOUT", str(exc)) from exc
    spec = snapshot.spec
    report_name = REPORT_NAMES[spec.report_type]
    subject = f"{spec.region}{spec.industry}"
    if report_name.startswith("产业") and subject.endswith("产业"):
        subject = subject[:-2]
    document.add_paragraph(subject + "\n" + report_name, "Report Cover Title")
    document.add_paragraph(f"{spec.period.from_} – {spec.period.to} 年", "Report Cover Period")
    if isinstance(spec, m.Comparison):
        document.add_paragraph("对标企业  " + "、".join(spec.companies), "Report Cover Details")
    document.add_paragraph(f"报告版本  {snapshot.version_id}", "Report Cover Details")
    document.add_paragraph(f"编制日期  {snapshot.created_at.date().isoformat()}", "Report Cover Details")
    document.add_page_break()
    document.add_paragraph("目录", "Report Contents")
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
                paragraph = document.add_paragraph(block.text)
                _append_citation(paragraph, citations.markers(block.evidence_ids,block.metric_ids))
            elif isinstance(block, m.ChartRef):
                chart = charts[block.chart_id]
                asset = assets[block.chart_id]
                picture = document.add_paragraph()
                picture.alignment = WD_ALIGN_PARAGRAPH.CENTER
                picture.paragraph_format.first_line_indent = Cm(0)
                picture.paragraph_format.keep_with_next = True
                section = document.sections[-1]
                width = min(section.page_width-section.left_margin-section.right_margin,
                            Cm(13) * asset.width / asset.height)
                picture.add_run().add_picture(BytesIO(asset.content), width=int(width))
                caption = document.add_paragraph(f"{chart.title}（单位：{chart.unit}）", "Report Table Header")
                caption.alignment = WD_ALIGN_PARAGRAPH.CENTER
                caption.paragraph_format.keep_with_next = True
                source = document.add_paragraph("来源：" + (citations.markers(metric_ids=chart.source_metric_ids) or "快照未提供证据"), "Report Table Text")
                source.alignment = WD_ALIGN_PARAGRAPH.CENTER
            elif isinstance(block, m.Table):
                table = document.add_table(rows=1, cols=len(block.columns))
                table.style = "Table Grid"
                table.autofit = False
                widths = [max(4, min(24, len(title))) for title in block.columns]
                total = sum(widths)
                for column, weight in zip(table.columns, widths, strict=True):
                    column.width = Cm(16 * weight / total)
                borders = OxmlElement("w:tblBorders")
                for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
                    border = OxmlElement("w:" + edge)
                    border.set(qn("w:val"), "single" if edge in ("top","bottom","insideH") else "nil")
                    border.set(qn("w:sz"), "4")
                    border.set(qn("w:color"), "D4DEE5")
                    borders.append(border)
                table._tbl.tblPr.append(borders)
                margins = OxmlElement("w:tblCellMar")
                for edge in ("top", "bottom", "left", "right"):
                    margin = OxmlElement("w:" + edge)
                    margin.set(qn("w:w"), "90" if edge in ("top","bottom") else "120")
                    margin.set(qn("w:type"), "dxa")
                    margins.append(margin)
                table._tbl.tblPr.append(margins)
                numeric = [all(re.fullmatch(r"[+-]?[0-9][0-9,.]*(?:%|％|\s*[a-zA-Z]+)?|[-—–]", row[i].text.strip()) for row in block.rows)
                           for i in range(len(block.columns))]

                for cell, title in zip(table.rows[0].cells, block.columns, strict=True):
                    cell.text = title
                marker = OxmlElement("w:tblHeader")
                marker.set(qn("w:val"), "true")
                table.rows[0]._tr.get_or_add_trPr().append(marker)
                for row in block.rows:
                    for cell, fact in zip(table.add_row().cells, row, strict=True):
                        cell.text = fact.text
                        _append_citation(cell.paragraphs[0], citations.markers(fact.evidence_ids,fact.metric_ids))
                for row_index, row in enumerate(table.rows):
                    for index, cell in enumerate(row.cells):
                        cell.width = table.columns[index].width
                        paragraph = cell.paragraphs[0]
                        paragraph.style = "Report Table Header" if row_index == 0 else "Report Table Text"
                        if numeric[index]:
                            paragraph.alignment = WD_ALIGN_PARAGRAPH.RIGHT
                        if row_index == 0 or row_index % 2 == 0:
                            shading = OxmlElement("w:shd")
                            shading.set(qn("w:fill"), "E4EDF3" if row_index == 0 else "F5F8FA")
                            cell._tc.get_or_add_tcPr().append(shading)
    warnings = [issue for issue in snapshot.quality.issues if issue.severity == "WARN"]
    if warnings:
        document.add_heading("数据与证据说明", level=1)
        for issue in warnings:
            location = issue.location
            chapter_title = chapters[location.chapter_id].title if location.chapter_id in chapters else location.chapter_id
            place = " / ".join(part for part in (chapter_title,location.block_id) if part) or "全文"
            document.add_paragraph(f"{place} | {issue.code}：{issue.description}")
    if citations.numbers:
        document.add_heading("参考来源", level=1)
        for entry in citations.entries():
            paragraph = document.add_paragraph(entry, "Report Table Text")
            paragraph.paragraph_format.space_after = Pt(8)
    content = BytesIO()
    document.save(content)
    return content.getvalue()
