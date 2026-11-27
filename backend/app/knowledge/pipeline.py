"""Worker-owned single-version parsing and index publication."""
from contextlib import closing
import json
from ..contracts import models as m
from .metrics import MetricRepository
from .text_repository import TextRepository

class IngestPipeline:
    def __init__(self, connect, runtime_root, index):
        self.connect=connect
        self.metrics=MetricRepository(connect,runtime_root)
        self.text=TextRepository(connect,runtime_root)
        self.index=index

    def process(self, version, *, labels=None, mapping=None):
        with closing(self.connect()) as connection:
            connection.begin()
            with connection.cursor() as cursor:
                cursor.execute("SELECT ingest_status,original_filename FROM document_versions WHERE document_version_id=%s FOR UPDATE",(version,))
                row=cursor.fetchone()
                if row is None:
                    connection.rollback()
                    raise ValueError("unknown version")
                if row[0] in ("READY","PROCESSING"):
                    connection.rollback()
                    return False
                cursor.execute("UPDATE document_versions SET ingest_status='PROCESSING',error_json=NULL WHERE document_version_id=%s",(version,))
            connection.commit()
        try:
            if row[1].lower().endswith((".xlsx",".csv")):
                self.metrics.ingest_version(version,**(mapping or {}))
            else:
                industries=(labels or {}).get("industries",[])
                self.text.ingest_version(version,industry=industries[0] if len(industries)==1 else None)
            with closing(self.connect()) as connection, connection.cursor() as cursor:
                cursor.execute("SELECT payload FROM evidences WHERE document_version_id=%s ORDER BY evidence_id",(version,))
                evidences=[m.Evidence.model_validate_json(row[0]) for row in cursor.fetchall()]
            if not evidences:
                raise ValueError("no indexable evidence")
            self.index.replace(version,evidences)
            with closing(self.connect()) as connection:
                connection.begin()
                with connection.cursor() as cursor:
                    cursor.execute("INSERT INTO knowledge_labels VALUES (%s,%s) ON DUPLICATE KEY UPDATE payload=VALUES(payload)",
                        (version,json.dumps(labels or {},ensure_ascii=False)))
                    cursor.execute("UPDATE document_versions SET ingest_status='READY',error_json=NULL WHERE document_version_id=%s AND ingest_status='PROCESSING'",(version,))
                connection.commit()
            return True
        except Exception:
            with closing(self.connect()) as connection, connection.cursor() as cursor:
                error=m.ErrorInfo(code="DOCUMENT_PROCESSING_FAILED",message="Document processing failed",retryable=True,details={})
                cursor.execute("UPDATE document_versions SET ingest_status='FAILED',error_json=%s WHERE document_version_id=%s",
                    (error.model_dump_json(),version))
                connection.commit()
            raise

    def recover_abandoned(self, version):
        """Call only after the worker confirms that the previous owner has stopped."""
        with closing(self.connect()) as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE document_versions SET ingest_status='FAILED',error_json=%s "
                "WHERE document_version_id=%s AND ingest_status='PROCESSING'",
                (m.ErrorInfo(code="DOCUMENT_PROCESSING_FAILED",message="Worker stopped",retryable=True,
                    details={}).model_dump_json(),version))
            connection.commit()
            return cursor.rowcount == 1
