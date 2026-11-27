"""Requires isolated MySQL via the existing W1_MYSQL_PORT convention."""
from pathlib import Path
from unittest.mock import Mock
from docx import Document
import test_knowledge_mysql
from backend.app.knowledge.pipeline import IngestPipeline
from backend.app.knowledge.retrieval import Retrieval
from backend.app.contracts import models as m

class PipelineTests(test_knowledge_mysql.KnowledgeMySQLTests):
    def test_failure_retry_ready_and_retrieval(self):
        connect=lambda:self.connect(self.database)
        with connect() as db, db.cursor() as cursor:
            for name in ("011_knowledge_metrics.sql","012_knowledge_index.sql"):
                for sql in Path("backend/migrations",name).read_text(encoding="utf-8-sig").split(";"):
                    if sql.strip():cursor.execute(sql)
        path=self.root/"source.docx"
        document=Document();document.add_paragraph("新能源汽车研发投入");document.save(path)
        accepted=self.ingest.ingest_document(path,self.metadata)
        version=accepted.document_version_id
        index=Mock();index.replace.side_effect=RuntimeError("partial index")
        pipeline=IngestPipeline(connect,self.root/"runtime",index)
        with self.assertRaises(RuntimeError):pipeline.process(version,labels={"entity_ids":["SAIC"]})
        service=Retrieval(connect,index)
        self.assertEqual(service.retrieve("研发投入",m.Filters(),5,[version]),[])
        index.replace.side_effect=None
        self.assertTrue(pipeline.process(version,labels={"entity_ids":["SAIC"]}))
        evidence=index.replace.call_args.args[1][0]
        index.rank.return_value=[(evidence.evidence_id,0.9)]
        result=service.retrieve("研发投入",m.Filters(entity_ids=["SAIC"]),5,[version])
        self.assertEqual(result[0].evidence_id,evidence.evidence_id)
        self.assertFalse(pipeline.process(version))
        self.assertEqual(service.retrieve("研发投入",m.Filters(entity_ids=["OTHER"]),5,[version]),[])
        self.assertEqual(service.retrieve("研发投入",m.Filters(),5,["unrelated"]),[])
    def test_real_model_frozen_versions(self):
        import os
        if not os.getenv("W4_REAL_MODEL"):
            self.skipTest("set W4_REAL_MODEL=1 for the real embedding model")
        from backend.app.knowledge.index import EvidenceIndex
        connect=lambda:self.connect(self.database)
        with connect() as db, db.cursor() as cursor:
            for name in ("011_knowledge_metrics.sql","012_knowledge_index.sql"):
                for sql in Path("backend/migrations",name).read_text(encoding="utf-8-sig").split(";"):
                    if sql.strip():cursor.execute(sql)
        index=EvidenceIndex(self.root/"index")
        self.addCleanup(index.client._system.stop)
        pipeline=IngestPipeline(connect,self.root/"runtime",index)
        versions=[]
        for amount in ("100","200"):
            path=self.root/"研发投入.docx"
            document=Document();document.add_paragraph("本期费用化研发投入为"+amount+"亿元。");document.save(path)
            accepted=self.ingest.ingest_document(path,self.metadata)
            self.assertTrue(pipeline.process(accepted.document_version_id,labels={"entity_ids":["SAIC"]}))
            versions.append(accepted.document_version_id)
        service=Retrieval(connect,index)
        for version,amount in zip(versions,("100","200")):
            result=service.retrieve("本期费用化研发投入是多少？",m.Filters(entity_ids=["SAIC"]),5,[version])
            self.assertTrue(result)
            self.assertTrue(all(e.document_version_id==version and amount in e.excerpt for e in result))
        self.assertEqual(service.retrieve("如何制作红烧肉？",m.Filters(),5,versions),[])
