from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import date
import csv
import unittest
from openpyxl import Workbook
from backend.app.contracts.models import DocumentRecord
from backend.app.knowledge.tabular import COLUMNS, parse_tabular
from backend.app.contracts.services import ServiceError

class TabularTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "original"
        self.record = DocumentRecord(document_id="doc", document_version_id="version",
            title="test", publisher="publisher", published_at=date(2024,1,1),
            ingest_status="QUEUED", error=None)
        self.row = ["sales","Sales","A","2024","year","CN","retail","1.20","万辆","10000","辆",""]

    def test_csv_bom_and_missing_zero(self):
        with self.path.open("w", encoding="utf-8-sig", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(COLUMNS)
            for entity, raw in [("A","1.20"),("B","0"),("C","—")]:
                row = self.row.copy()
                row[2], row[7] = entity, raw
                writer.writerow(row)
        output = parse_tabular(self.path, "source.csv", self.record)
        self.assertEqual([x.metric.value for x in output], ["12000","0",None])
        self.assertEqual(output[0].evidence.locator.row, 2)
        self.assertEqual(output, parse_tabular(self.path, "source.csv", self.record))

    def test_xlsx_without_cached_formula(self):
        book = Workbook()
        sheet = book.active
        sheet.title = "Source"
        sheet.append(COLUMNS)
        row = self.row.copy()
        row[7] = "=1+2"
        sheet.append(row)
        book.save(self.path)
        observation = parse_tabular(self.path, "source.xlsx", self.record)[0]
        self.assertIsNone(observation.metric.value)
        self.assertEqual(observation.evidence.locator.cells, "H2")

    def test_conflicting_rows_fail(self):
        with self.path.open("w", encoding="utf-8", newline="") as file:
            writer = csv.writer(file)
            writer.writerow(COLUMNS)
            writer.writerows([self.row,self.row])
        with self.assertRaises(ServiceError):
            parse_tabular(self.path, "source.csv", self.record)
