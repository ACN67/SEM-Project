from contextlib import closing
from datetime import date
import csv
from pathlib import Path
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

    def test_linked_observation_keeps_original_boundary_and_date(self):
        def ingest(name, marker):
            path=self.root/name
            with path.open("w",encoding="utf8",newline="") as stream:
                writer=csv.writer(stream);writer.writerow([*COLUMNS,"provenance"])
                writer.writerow(["sales","Sales","A","2023","year","CN","retail","10","cars","1","cars","",marker])
            return self.ingest.ingest_document(path,self.metadata.model_copy(update={"published_at":date(2023,4,1)})).document_version_id
        source=ingest("original.csv","original")
        original=self.metrics.ingest_version(source)[0]
        def ready(version):
            with closing(self.connect(self.database)) as connection,connection.cursor() as cursor:
                cursor.execute("UPDATE document_versions SET ingest_status='READY' WHERE document_version_id=%s",(version,))
                connection.commit()
        ready(source)
        linked=ingest("linked.csv","transcription")
        self.metrics.ingest_version(linked,source_evidence_ids=original.source_evidence_ids)
        ready(linked)
        self.assertEqual(self.metrics.get_metrics(Filters(),[linked]),[])
        visible=self.metrics.get_metrics(Filters(as_of=date(2024,12,31)),[linked,source])
        self.assertEqual(len(visible),2)
        self.assertTrue(all(x.source_evidence_ids==original.source_evidence_ids for x in visible))
        self.assertEqual(self.metrics.get_metrics(Filters(as_of=date(2023,3,31)),[linked,source]),[])
        with closing(self.connect(self.database)) as connection,connection.cursor() as cursor:
            cursor.execute("UPDATE document_versions SET ingest_status='FAILED' WHERE document_version_id=%s",(source,))
            connection.commit()
        self.assertEqual(self.metrics.get_metrics(Filters(),[linked,source]),[])

    def test_pipeline_persists_industry_for_case_filters(self):
        from unittest.mock import MagicMock
        from backend.app.knowledge.pipeline import IngestPipeline
        path=self.root/'case.csv'
        with path.open('w',encoding='utf8',newline='') as stream:
            writer=csv.writer(stream);writer.writerow(COLUMNS)
            writer.writerow(['sales','Sales','','2024','year','德国','BEV','10','辆','1','辆',''])
        version=self.ingest.ingest_document(path,self.metadata).document_version_id
        with closing(self.connect(self.database)) as connection,connection.cursor() as cursor:
            for statement in next(Path('backend/migrations').glob('012*')).read_text().split(';'):
                if statement.strip():cursor.execute(statement)
        index=MagicMock();index.fingerprint='test'
        pipeline=IngestPipeline(lambda:self.connect(self.database),self.root/'runtime',index)
        pipeline.process(version,labels={'industries':['新能源汽车']})
        filters=Filters(industries=['新能源汽车'],regions=['德国'],indicators=['sales'],periods=['2024'],as_of=date(2024,1,1))
        self.assertEqual(len(self.metrics.get_metrics(filters,[version])),1)
        self.assertEqual(self.metrics.get_metrics(filters.model_copy(update={'industries':['other']}),[version]),[])
        self.assertEqual(self.metrics.get_metrics(filters.model_copy(update={'regions':['other']}),[version]),[])
