"""Worker-owned single-version parsing and index publication."""
from contextlib import closing
import json
from ..contracts import models as m
from ..contracts.services import ServiceError
from .errors import infrastructure_error
from .metrics import MetricRepository
from .text_repository import TextRepository

class IngestPipeline:
    def __init__(self, connect, runtime_root, index):
        self.connect=connect
        self.metrics=MetricRepository(connect,runtime_root)
        self.text=TextRepository(connect,runtime_root)
        self.index=index

    def process(self, version, *, labels=None, mapping=None, rebuild=False):
        try:
            return self._process(version, labels=labels, mapping=mapping, rebuild=rebuild)
        except Exception as exc:
            if isinstance(exc, ServiceError):
                raise
            raise infrastructure_error(exc) from exc

    def _process(self, version, *, labels=None, mapping=None, rebuild=False):
        with closing(self.connect()) as connection:
            connection.begin()
            with connection.cursor() as cursor:
                cursor.execute("SELECT ingest_status,original_filename FROM document_versions WHERE document_version_id=%s FOR UPDATE",(version,))
                row=cursor.fetchone()
                if row is None:
                    connection.rollback()
                    raise ValueError("unknown version")
                if row[0] == "PROCESSING" or (row[0] == "READY" and not rebuild):
                    connection.rollback()
                    return False
                if rebuild:
                    cursor.execute("SELECT payload FROM knowledge_labels WHERE document_version_id=%s", (version,))
                    stored = cursor.fetchone()
                    if stored is None:
                        connection.rollback()
                        raise ServiceError(m.ErrorInfo(code="DOCUMENT_PROCESSING_FAILED",
                            message="No published index manifest; retry ingestion with original labels and mapping",
                            retryable=False, details={"reason": "rebuild_requires_published_manifest"}))
                    labels = json.loads(stored[0])
                cursor.execute("UPDATE document_versions SET ingest_status='PROCESSING',error_json=NULL WHERE document_version_id=%s",(version,))
            connection.commit()
        try:
            if not rebuild:
                if row[1].lower().endswith((".xlsx",".csv")):
                    industry_values=(labels or {}).get("industries",[])
                    tabular_mapping=dict(mapping or {})
                    if len(industry_values)==1:
                        if "industry" in tabular_mapping and tabular_mapping["industry"]!=industry_values[0]:
                            raise ValueError("conflicting tabular industry labels")
                        tabular_mapping["industry"]=industry_values[0]
                    self.metrics.ingest_version(version,**tabular_mapping)
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
                        (version,json.dumps({**(labels or {}), "_index_fingerprint":self.index.fingerprint},ensure_ascii=False)))
                    cursor.execute("UPDATE document_versions SET ingest_status='READY',error_json=NULL WHERE document_version_id=%s AND ingest_status='PROCESSING'",(version,))
                connection.commit()
            return True
        except Exception as exc:
            failure = infrastructure_error(exc, "DOCUMENT_PROCESSING_FAILED")
            with closing(self.connect()) as connection, connection.cursor() as cursor:
                error=failure.error
                cursor.execute("UPDATE document_versions SET ingest_status='FAILED',error_json=%s WHERE document_version_id=%s",
                    (error.model_dump_json(),version))
                connection.commit()
            raise failure from exc

    def recover_abandoned(self, version):
        """Call only after the worker confirms that the previous owner has stopped."""
        with closing(self.connect()) as connection, connection.cursor() as cursor:
            cursor.execute("UPDATE document_versions SET ingest_status='FAILED',error_json=%s "
                "WHERE document_version_id=%s AND ingest_status='PROCESSING'",
                (m.ErrorInfo(code="DOCUMENT_PROCESSING_FAILED",message="Worker stopped",retryable=True,
                    details={}).model_dump_json(),version))
            connection.commit()
            return cursor.rowcount == 1
