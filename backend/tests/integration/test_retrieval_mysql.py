"""Requires isolated MySQL via the existing W1_MYSQL_PORT convention."""
from pathlib import Path
from unittest.mock import Mock
from docx import Document
import test_knowledge_mysql
from backend.app.knowledge.pipeline import IngestPipeline
from backend.app.knowledge.retrieval import Retrieval
from backend.app.contracts import models as m
from backend.app.contracts.services import ServiceError

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
        index=Mock();index.fingerprint="test-index";index.replace.side_effect=RuntimeError("partial index")
        pipeline=IngestPipeline(connect,self.root/"runtime",index)
        with self.assertRaises(ServiceError):pipeline.process(version,labels={"entity_ids":["SAIC"]})
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
        index.collection.delete(where={"version":versions[0]})
        with self.assertRaises(ServiceError) as err:
            service.retrieve("研发投入",m.Filters(),5,[versions[0]])
        self.assertEqual(err.exception.error.code,"INDEX_NOT_READY")
        self.assertTrue(pipeline.process(versions[0],rebuild=True))
        self.assertTrue(service.retrieve("本期费用化研发投入",m.Filters(entity_ids=["SAIC"]),5,[versions[0]]))


        changed=EvidenceIndex(self.root/"index", revision="0"*40,model=index.model)
        changed_service=Retrieval(connect,changed)
        with self.assertRaises(ServiceError) as err:
            changed_service.retrieve("研发投入",m.Filters(),5,[versions[0]])
        self.assertEqual(err.exception.error.code,"INDEX_NOT_READY")
        changed_pipeline=IngestPipeline(connect,self.root/"runtime",changed)
        self.assertTrue(changed_pipeline.process(versions[0],rebuild=True))
        self.assertTrue(changed_service.retrieve("本期费用化研发投入",m.Filters(entity_ids=["SAIC"]),5,[versions[0]]))

    def test_parser_error_matches_persisted_status(self):
        from unittest.mock import patch
        import json
        connect=lambda:self.connect(self.database)
        path=self.root/"source.docx"
        document=Document();document.add_paragraph("valid original");document.save(path)
        version=self.ingest.ingest_document(path,self.metadata).document_version_id
        failure=ServiceError(m.ErrorInfo(code="DOCUMENT_PARSE_FAILED",message="Cannot parse",
            retryable=False,details={}))
        pipeline=IngestPipeline(connect,self.root/"runtime",Mock())
        with patch.object(pipeline.text,"ingest_version",side_effect=failure):
            with self.assertRaises(ServiceError) as err:pipeline.process(version)
        with connect() as db, db.cursor() as cursor:
            cursor.execute("SELECT error_json,ingest_status FROM document_versions WHERE document_version_id=%s",(version,))
            payload,status=cursor.fetchone()
        self.assertEqual(status,"FAILED")
        self.assertEqual(json.loads(payload),err.exception.error.model_dump())


    def test_rebuild_preserves_labels_and_parsed_rows_without_original_mapping(self):
        import csv
        from unittest.mock import patch
        from backend.app.knowledge.tabular import COLUMNS
        connect=lambda:self.connect(self.database)
        with connect() as db, db.cursor() as cursor:
            for name in ("011_knowledge_metrics.sql","012_knowledge_index.sql"):
                for sql in Path("backend/migrations",name).read_text(encoding="utf-8-sig").split(";"):
                    if sql.strip():cursor.execute(sql)
        index=Mock()
        index.fingerprint="initial-index"
        pipeline=IngestPipeline(connect,self.root/"runtime",index)
        for extension in ("csv","docx"):
            path=self.root/("source."+extension)
            if extension=="csv":
                headers=["mapped-indicator",*COLUMNS[1:]]
                with path.open("w",encoding="utf8",newline="") as file:
                    writer=csv.writer(file);writer.writerow(headers)
                    writer.writerow(["sales","Sales","A","2024","year","CN","retail","100","cars","1","cars",""])
                mapping={"header_mapping":{"mapped-indicator":"indicator"},"industry":"auto"}
            else:
                doc=Document();doc.add_paragraph("研发投入 100 亿元");doc.save(path)
                mapping=None
            version=self.ingest.ingest_document(path,self.metadata).document_version_id
            labels={"entity_ids":["A"],"industries":["auto"],"regions":["CN"]}
            pipeline.process(version,labels=labels,mapping=mapping)
            before=index.replace.call_args.args[1]
            index.rank.return_value=[(before[0].evidence_id,0.9)]
            filters=m.Filters(entity_ids=["A"],industries=["auto"],regions=["CN"])
            service=Retrieval(connect,index)
            self.assertTrue(service.retrieve("投入",filters,5,[version]))
            with connect() as db, db.cursor() as cursor:
                cursor.execute("SELECT payload FROM metrics WHERE document_version_id=%s",(version,))
                metrics_before=cursor.fetchall()
            index.fingerprint="replacement-index"
            with patch.object(pipeline.text,"ingest_version",side_effect=AssertionError("reparsed original")), patch.object(
                pipeline.metrics,"ingest_version",side_effect=AssertionError("reparsed rows")):
                self.assertTrue(pipeline.process(version,rebuild=True))
            self.assertEqual(before,index.replace.call_args.args[1])
            self.assertTrue(service.retrieve("投入",filters,5,[version]))
            with connect() as db, db.cursor() as cursor:
                cursor.execute("SELECT payload FROM metrics WHERE document_version_id=%s",(version,))
                self.assertEqual(metrics_before,cursor.fetchall())
                cursor.execute("SELECT payload FROM knowledge_labels WHERE document_version_id=%s",(version,))
                import json
                stored=json.loads(cursor.fetchone()[0])
                self.assertEqual(stored,{**labels,"_index_fingerprint":"replacement-index"})
