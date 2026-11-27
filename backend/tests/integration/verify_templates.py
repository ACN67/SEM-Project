"""Controlled template export regression, run as a module with converter and output paths."""
import json
from pathlib import Path
import sys
from docx import Document
from pypdf import PdfReader
from backend.app.contracts import models as m
from backend.app.templates.catalog import TemplateCatalog
from backend.app.export.service import ExportService
from backend.app.export.pdf import PdfConverter
from backend.app.quality.render import render_chart_png
from backend.tests.unit.test_export_evidence import snapshot

def verify(executable, root):
    root.mkdir(parents=True, exist_ok=True)
    catalog = TemplateCatalog()
    examples = Path("templates/examples")
    results = []
    for template in catalog.list_templates():
        payload = json.loads((examples/(template.report_type+".json")).read_text(encoding="utf8"))
        spec, _ = catalog.validate_request(payload)
        doc = snapshot()
        doc.spec = spec
        doc.version_id = template.report_type+"-controlled-v1"
        doc.outline.chapters = template.sections
        prototype = doc.chapters[0]
        chart = doc.charts[0]
        doc.chapters = []
        chart_task = next((t for t in template.sections if t.desired_chart), None)
        doc.charts = []
        for task in template.sections:
            text = "受控渲染样例。"+task.research_questions[0]
            blocks = [m.Paragraph(type="paragraph", block_id=task.chapter_id+"-text",
                text=text, evidence_ids=["ev1"], metric_ids=[])]
            if chart_task and task.chapter_id == chart_task.chapter_id:
                chart.chapter_id = task.chapter_id
                chart.block_id = task.chapter_id+"-chart"
                chart.type = task.desired_chart
                blocks.append(m.ChartRef(type="chart_ref", block_id=chart.block_id, chart_id=chart.chart_id))
                doc.charts.append(chart)
            blocks.append(m.Table(type="table", block_id=task.chapter_id+"-table",
                columns=["核验事项","证据边界"], rows=[[m.Cell(text="来源定位",evidence_ids=["ev1"],metric_ids=[]),
                m.Cell(text="缺少一侧证据时标记待核实",evidence_ids=[],metric_ids=[])]]))
            doc.chapters.append(prototype.model_copy(update=dict(chapter_id=task.chapter_id,title=task.title,
                blocks=blocks,chart_requests=[],missing_evidence=[]),deep=True))
        doc.quality.issues[0].location = m.Location(chapter_id=template.sections[0].chapter_id,
            block_id=template.sections[0].chapter_id+"-text")
        service = ExportService(root,converter=PdfConverter(executable),
            asset_resolver=lambda v,a:render_chart_png(chart))
        for format in ("docx","pdf"):
            artifact = service.render_report(doc,format)
            path = root/artifact.storage_key
            if format=="docx":
                rendered=Document(path)
                text=" ".join(p.text for p in rendered.paragraphs)
                assert len(rendered.inline_shapes)==len(doc.charts)
            else:
                reader=PdfReader(path)
                text=" ".join(p.extract_text() for p in reader.pages)
            for title in [t.title for t in template.sections]:
                assert title in text, (template.report_type,title)
            assert doc.version_id in text and "参考来源" in text and "模拟数据尚需人工核对" in text
            results.append(dict(type=template.report_type,format=format,**artifact.model_dump()))
    assert len(results)==10
    (root/"result.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf8")

if __name__=="__main__":
    verify(Path(sys.argv[1]),Path(sys.argv[2]))
