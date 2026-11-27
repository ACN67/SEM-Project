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
from .specialized_content import TEXT, prepare_sources

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
        prepare_sources(doc, template.report_type)
        doc.version_id = template.report_type+"-controlled-v1"
        from backend.app.generation.outline import plan_outline
        doc.outline = plan_outline(spec, template)
        prototype = doc.chapters[0]
        chart = doc.charts[0]
        doc.chapters = []
        chart_task = next((t for t in template.sections if t.desired_chart), None)
        doc.charts = []
        if template.report_type=="industry_prosperity":
            chart.title="季度销量及同期比较（受控样例）"
            chart.source_metric_ids=[]
            chart.data=[]
            doc.metrics=[]
            for year,values in [(2023,[100,100]),(2024,[130,120])]:
                for quarter,value in enumerate(values,1):
                    metric=snapshot().metrics[0].model_copy(update=dict(metric_id=f"q{year}{quarter}",
                        period=f"{year}-Q{quarter}",granularity="quarter",value=str(value),
                        raw_value=str(value),input_metric_ids=[],formula_id=None),deep=True)
                    doc.metrics.append(metric)
                    chart.source_metric_ids.append(metric.metric_id)
                    chart.data.append(m.Point(label=metric.period,series="季度销量",value=str(value),metric_id=metric.metric_id))

        for task in template.sections:
            text = "受控渲染样例。"+task.research_questions[0]
            text=TEXT.get(template.report_type,{}).get(task.section_key,text)

            blocks = [m.Paragraph(type="paragraph", block_id=task.chapter_id+"-text",
                text=text, evidence_ids=(["ev2"] if template.report_type=="investment_attraction" and task.section_key=="capability" else ["ev1","ev2"] if template.report_type=="investment_attraction" and task.section_key in ("summary","matching") else ["ev2"] if template.report_type=="overseas_assessment" and task.section_key=="policy" else ["ev1"]), metric_ids=[])]
            if chart_task and task.chapter_id == chart_task.chapter_id:
                chart.chapter_id = task.chapter_id
                chart.block_id = task.chapter_id+"-chart"
                chart.type = task.desired_chart
                blocks.append(m.ChartRef(type="chart_ref", block_id=chart.block_id, chart_id=chart.chart_id))
                doc.charts.append(chart)
            table_columns=["核验事项","证据边界"]
            table_rows=[["来源定位","缺少一侧证据时标记待核实"]]
            if template.report_type=="investment_attraction":
                table_columns=["区域需求及来源","企业能力及来源","判断"]
                table_rows=[["动力电池配套需求","电芯测试能力","候选匹配，需人工复核"],
                    ["缺少需求侧证据","保留能力侧证据","待核实，不推断签约"]]
            elif template.report_type=="overseas_assessment":
                table_columns=["事项","对象和日期","证据范围"]
                table_rows=[["市场统计","德国 BEV 全年新注册","与进口量区分"],
                    ["反补贴措施","中国原产新 BEV；2024-10-30 生效","仅为受控展示样例"],
                    ["普通关税","税目及适用时点待核验","未计入普通关税，无法给出完整税负"]]
            elif template.report_type=="industry_prosperity":
                table_columns=["季度","2023 同期","2024 本期","同比"]
                table_rows=[["Q1","100","130","30%"],["Q2","100","120","20%"],
                    ["缺基期","缺失","120","不计算"],["零基期","0","120","无定义"]]
            table_sections={"industry_prosperity":"quarterly","investment_attraction":"matching","overseas_assessment":"policy"}
            if task.section_key==table_sections.get(template.report_type,"market"):
                blocks.append(m.Table(type="table", block_id=task.chapter_id+"-table",
                    columns=table_columns, rows=[[m.Cell(text=value,evidence_ids=(["ev1"] if col==0 and ri==0 else ["ev2"] if col==1 else []) if template.report_type=="investment_attraction" else ["ev2"] if template.report_type=="overseas_assessment" and ri==1 else [] if template.report_type=="overseas_assessment" and ri==2 else ["ev1"],metric_ids=[]) for col,value in enumerate(row)] for ri,row in enumerate(table_rows)]))
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
                text+=" ".join(cell.text for table in rendered.tables for row in table.rows for cell in row.cells)
                if template.report_type=="investment_attraction":
                    cells=rendered.tables[0].rows[1].cells
                    assert "[1]" in cells[0].text and "[2]" not in cells[0].text
                    assert "[2]" in cells[1].text and "[1]" not in cells[1].text
                if template.report_type in TEXT:
                    assert all(TEXT[template.report_type][task.section_key] in text for task in template.sections)
                assert len(rendered.inline_shapes)==len(doc.charts)
            else:
                reader=PdfReader(path)
                text=" ".join(p.extract_text() for p in reader.pages)
            for title in [t.title for t in template.sections]:
                assert title in text, (template.report_type,title)
            assert doc.version_id in text and "参考来源" in text and "模拟数据尚需人工核对" in text
            results.append(dict(type=template.report_type,format=format,**artifact.model_dump()))
        if template.report_type=="investment_attraction":
            negative=doc.model_copy(deep=True)
            negative.version_id="investment-missing-demand"
            negative.evidences=[e for e in negative.evidences if e.evidence_id=="ev2"]
            for chapter in negative.chapters:
                chapter.blocks=[m.Paragraph(type="paragraph",block_id=chapter.chapter_id+"-gap",
                    text="需求侧证据缺失，候选匹配待核实，不推断签约。",evidence_ids=["ev2"],metric_ids=[])]
            artifact=service.render_report(negative,"docx")
            rendered=Document(root/artifact.storage_key)
            text=" ".join(p.text for p in rendered.paragraphs)
            assert "待核实" in text and "受控企业电芯测试能力说明" in text
            assert "受控区域需求说明" not in text
    assert len(results)==10
    (root/"result.json").write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding="utf8")

if __name__=="__main__":
    verify(Path(sys.argv[1]),Path(sys.argv[2]))
