from io import BytesIO
from pathlib import Path
import unittest
from docx import Document
from backend.app.contracts.models import ReportDocument
from backend.app.contracts.services import ServiceError
from backend.app.export.word import render_docx

FIXTURE = Path(__file__).parent / "fixtures" / "word_snapshot.json"

class WordTests(unittest.TestCase):
    def setUp(self):
        self.snapshot = ReportDocument.model_validate_json(FIXTURE.read_text(encoding="utf8"))

    def test_document_order_fields_and_table(self):
        doc = Document(BytesIO(render_docx(self.snapshot)))
        headings = [p.text for p in doc.paragraphs if p.style.name == "Heading 1"]
        self.assertEqual(headings, ["市场规模","结论","参考来源"])
        self.assertEqual(len(doc.tables[0].rows),81)
        self.assertEqual(doc.tables[0].cell(1,1).text,"0")
        self.assertIn("tblHeader",doc.tables[0]._tbl.xml)
        self.assertIn("TOC",doc.element.xml)
        self.assertIn("PAGE",doc.sections[0].footer._element.xml)
        self.assertAlmostEqual(doc.sections[0].left_margin.cm,2.5,places=2)
        self.assertIn("eastAsia",doc.styles["Normal"].element.xml)

    def test_refusals(self):
        for changes, code in [({"exportable":False},"REPORT_NOT_EXPORTABLE"),({"layout_version":"other"},"UNKNOWN_LAYOUT")]:
            with self.subTest(code=code), self.assertRaises(ServiceError) as err:
                render_docx(self.snapshot.model_copy(update=changes))
            self.assertEqual(err.exception.error.code, code)
        chart = ReportDocument.model_validate_json(Path("shared/contracts/fixtures/report_document.json").read_text(encoding="utf8"))
        with self.assertRaises(ServiceError) as err:
            render_docx(chart)
        self.assertEqual(err.exception.error.code,"EXPORT_FAILED")


    def test_frozen_facts_require_existing_references(self):
        from backend.app.contracts.models import Fact
        for field in ("evidence_ids", "metric_ids"):
            fact = Fact(text="Evidence check", evidence_ids=[], metric_ids=[])
            setattr(fact, field, ["unknown"])
            with self.assertRaises(ServiceError) as err:
                render_docx(self.snapshot.model_copy(update={"facts":[fact]}))
            self.assertEqual(err.exception.error.code,"INVALID_SNAPSHOT")
        valid = Fact(text="Valid", evidence_ids=["ev1"], metric_ids=["m1"])
        self.assertTrue(render_docx(self.snapshot.model_copy(update={"facts":[valid]})))

    def test_visual_layout_styles(self):
        doc = Document(BytesIO(render_docx(self.snapshot)))
        self.assertEqual(doc.paragraphs[0].style.name, "Report Cover Title")
        self.assertIn("新能源汽车", doc.paragraphs[0].text)
        self.assertEqual(doc.tables[0].rows[0].cells[0].paragraphs[0].style.name, "Report Table Header")
        self.assertEqual(doc.tables[0].cell(1,1).paragraphs[0].alignment, 2)
        self.assertIn("E4EDF3",doc.tables[0]._tbl.xml)
