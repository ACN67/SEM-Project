from contextlib import closing
from datetime import date
import csv
import unittest
from test_knowledge_mysql import KnowledgeMySQLTests as Base
from backend.app.contracts.models import Filters
from backend.app.knowledge.metrics import MetricRepository
from backend.app.knowledge.tabular import COLUMNS

class MetricMySQLTests(Base):
    def setUp(self):
        super().setUp()
        with closing(self.connect(self.database)) as connection, connection.cursor() as cursor:
            from pathlib import Path
            for statement in Path("backend/migrations/011_knowledge_metrics.sql").read_text().split(";"):
                if statement.strip():
                    cursor.execute(statement)
        self.metrics = MetricRepository(lambda: self.connect(self.database), self.root / "runtime")

    def test_metrics_transaction_and_frozen_query(self):
        path = self.root / "metrics.csv"
        with path.open("w", encoding="utf8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(COLUMNS)
            for entity in ("A","B"):
                for period in ("2023","2024"):
                    for scope, value in (("retail","0"),("wholesale","—")):
                        writer.writerow(["sales","Sales",entity,period,"year","CN",scope,value,"cars","1","cars",""])
        accepted = self.ingest.ingest_document(path,self.metadata)
        version = accepted.document_version_id
        with closing(self.connect(self.database)) as connection, connection.cursor() as cursor:
            cursor.execute("CREATE TRIGGER reject_metric BEFORE INSERT ON metrics FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='test rollback'")
        with self.assertRaises(Exception):
            self.metrics.ingest_version(version, industry="auto")
        with closing(self.connect(self.database)) as connection, connection.cursor() as cursor:
            for table in ("chunks","evidences","metrics"):
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                self.assertEqual(cursor.fetchone()[0],0)
            cursor.execute("DROP TRIGGER reject_metric")
        first = self.metrics.ingest_version(version, industry="auto")
        self.assertEqual(first,self.metrics.ingest_version(version,industry="auto"))
        with self.assertRaises(ValueError):
            self.metrics.ingest_version(version,industry="other")
        self.assertEqual(len(first),8)
        self.assertEqual(self.metrics.get_metrics(Filters(),[version]),[])
        with closing(self.connect(self.database)) as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE document_versions SET ingest_status='READY' WHERE document_version_id=%s",(version,))
        self.assertEqual(len(self.metrics.get_metrics(Filters(),[version])),8)
        self.assertEqual(self.metrics.get_metrics(Filters(),[]),[])
        self.assertEqual(self.metrics.get_metrics(Filters(as_of=date(2023,1,1)),[version]),[])
        result = self.metrics.get_metrics(Filters(entity_ids=["A"],periods=["2024"],industries=["auto"]),[version])
        self.assertEqual(len(result),2)
        self.assertEqual({m.value for m in result},{"0",None})
