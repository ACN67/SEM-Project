"""K3 end-to-end frozen asset, citation and warning checks."""
from io import BytesIO
from pathlib import Path
import unittest
from docx import Document
from backend.app.contracts import models as m
from backend.app.contracts.services import ServiceError
from backend.app.export.word import render_docx
from backend.app.quality.render import render_chart_png

def snapshot():
    doc = m.ReportDocument.model_validate_json(Path("shared/contracts/fixtures/report_document.json").read_text(encoding="utf8"))
    first = doc.evidences[0]
    second = first.model_copy(update={"evidence_id":"ev2","title":"第二来源","locator":m.Pdf(kind="pdf",page=13)})
    doc.evidences.append(second)
    raw = doc.metrics[0]
    other = raw.model_copy(update={"metric_id":"m2","source_evidence_ids":["ev2"]})
    derived = raw.model_copy(update={"metric_id":"derived","source_evidence_ids":[],
                                     "input_metric_ids":["m1","m2"],"formula_id":"sum"})
    doc.metrics.extend([other,derived])
    chart = doc.charts[0]
    earlier = [raw.model_copy(update={"metric_id":f"m{year}","period":str(year),"value":str(value),"raw_value":str(value)})
               for year,value in ((2022,80),(2023,100))]
    doc.metrics.extend(earlier)
    derived.value = "240"
    derived.raw_value = None
    chart.source_metric_ids = [metric.metric_id for metric in [*earlier,derived]]
    chart.data = [m.Point(label=metric.period,series="国内新能源汽车销量研究样本",value=metric.value,metric_id=metric.metric_id)
                  for metric in [*earlier,derived]]
    chapter = doc.chapters[0]
    chapter.blocks[0].text = "模拟销量原始观测为 120 辆，合计指标见下图。"
    chapter.blocks.append(m.Table(type="table",block_id="tbl",columns=["指标","数值"],rows=[
        [m.Cell(text="合计",evidence_ids=[],metric_ids=[]),m.Cell(text="240",evidence_ids=[],metric_ids=["derived"])]
    ]))
    task = doc.outline.chapters[0]
    doc.outline.chapters.append(task.model_copy(update={"chapter_id":"ch_other","section_key":"other","title":"结论","order":1,"depends_on":["ch_market"]}))
    doc.chapters.append(chapter.model_copy(update={"chapter_id":"ch_other","title":"结论","blocks":[
        m.Paragraph(type="paragraph",block_id="p2",text="同一出处用于结论。",evidence_ids=["ev1"],metric_ids=[])],
        "chart_requests":[]}))
    doc.quality.issues.append(m.Issue(issue_id="warn",severity="WARN",code="SAMPLE_GAP",
        location=m.Location(chapter_id=chapter.chapter_id,block_id="tbl"),description="模拟数据尚需人工核对。",related_ids=[]))
    return doc

class EvidenceExportTests(unittest.TestCase):
    def test_chart_citations_and_warnings(self):
        snap = snapshot()
        png = render_chart_png(snap.charts[0],width=900,height=540)
        calls=[]
        def resolver(version,asset):
            calls.append((version,asset))
            return png
        doc=Document(BytesIO(render_docx(snap,asset_resolver=resolver)))
        self.assertEqual(calls,[(snap.version_id,snap.charts[0].asset_id)])
        self.assertEqual(len(doc.inline_shapes),1)
        texts=[p.text for p in doc.paragraphs]
        self.assertIn("来源：[1][2]",texts)
        self.assertEqual(doc.tables[0].cell(1,1).text,"240[1][2]")
        self.assertIn("同一出处用于结论。[1]",texts)
        self.assertEqual(sum(t.startswith("[1] ") for t in texts),1)
        self.assertEqual(sum(t.startswith("[2] ") for t in texts),1)
        self.assertTrue(any("表格（第 3 个内容块）" in t and "模拟数据尚需人工核对" in t for t in texts))
        self.assertIn("参考来源",texts)
        self.assertTrue(any("PDF 第 13 页" in t for t in texts))
        body=doc.element.body.xml
        self.assertLess(body.index("w:drawing"),body.index("w:tbl>"))

    def test_missing_corrupt_cross_version_and_cycle(self):
        snap=snapshot()
        for resolver in (lambda v,a:{("old","a1"):b"old"}[(v,a)],lambda v,a:b"invalid"):
            with self.assertRaises(ServiceError) as err:
                render_docx(snap,asset_resolver=resolver)
            self.assertEqual(err.exception.error.code,"EXPORT_FAILED")
            self.assertEqual(err.exception.error.details["chart_id"],"chart1")
        snap.metrics[0].input_metric_ids=["derived"]
        with self.assertRaises(ServiceError) as err:
            render_docx(snap,asset_resolver=lambda v,a:b"bad")
        self.assertIn("cyclic",str(err.exception))

    def test_chart_requires_every_original_evidence(self):
        for missing in (["m1", "m2", "m2022", "m2023"], ["m2"]):
            snap = snapshot()
            for metric in snap.metrics:
                if metric.metric_id in missing:
                    metric.source_evidence_ids = []
            with self.assertRaises(ServiceError) as err:
                render_docx(snap, asset_resolver=lambda v,a: self.fail("read asset before provenance check"))
            self.assertEqual(err.exception.error.code, "EXPORT_FAILED")
            self.assertEqual(err.exception.error.details["chart_id"], "chart1")
            self.assertIn(err.exception.error.details["metric_id"], missing)
        snap = snapshot()
        snap.metrics[1].source_evidence_ids = ["unknown"]
        with self.assertRaises(ServiceError) as err:
            render_docx(snap)
        self.assertEqual(err.exception.error.code, "EXPORT_FAILED")
        self.assertEqual(err.exception.error.details["metric_id"], "m2")
        snap = snapshot()
        for metric in snap.metrics:
            if not metric.input_metric_ids:
                metric.source_evidence_ids = ["ev1"]
        doc = Document(BytesIO(render_docx(snap, asset_resolver=lambda v,a:render_chart_png(snap.charts[0]))))
        texts = [p.text for p in doc.paragraphs]
        self.assertIn("来源：[1]", texts)
        self.assertEqual(sum(t.startswith("[1] ") for t in texts), 1)
        self.assertFalse(any(t.startswith("[2] ") for t in texts))

    def test_non_exportable_takes_priority_over_missing_chart_evidence(self):
        snap = snapshot()
        snap.metrics[1].source_evidence_ids = []
        for exportable, code in ((False, "REPORT_NOT_EXPORTABLE"), (True, "EXPORT_FAILED")):
            with self.subTest(exportable=exportable):
                snap.exportable = exportable
                with self.assertRaises(ServiceError) as err:
                    render_docx(snap, asset_resolver=lambda v,a: self.fail("read asset before rejection"))
                self.assertEqual(err.exception.error.code, code)
                if exportable:
                    self.assertEqual(err.exception.error.details, {
                        "chart_id": "chart1", "asset_id": "a1", "metric_id": "m2",
                    })

    def test_block_rejected(self):
        snap=snapshot()
        snap.quality.issues[0].severity="BLOCK"
        with self.assertRaises(ServiceError):
            render_docx(snap,asset_resolver=lambda v,a:b"bad")

if __name__ == "__main__":
    import sys
    snap=snapshot()
    png=render_chart_png(snap.charts[0],width=900,height=540)
    Path(sys.argv[1]).write_bytes(render_docx(snap,asset_resolver=lambda v,a:png))
