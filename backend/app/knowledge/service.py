"""Compose the public knowledge contract from the versioned repositories."""
from pathlib import Path
from .ingest import DocumentIngest
from .repository import DocumentRepository
from .storage import OriginalStorage
from .metrics import MetricRepository
from .pipeline import IngestPipeline
from .retrieval import Retrieval

class KnowledgeService:
    def __init__(self, connect, runtime_root: Path, index):
        self.ingest=DocumentIngest(OriginalStorage(runtime_root),DocumentRepository(connect))
        self.metrics=MetricRepository(connect,runtime_root)
        self.pipeline=IngestPipeline(connect,runtime_root,index)
        self.retrieval=Retrieval(connect,index)

    def ingest_document(self, file, metadata):
        return self.ingest.ingest_document(file,metadata)

    def get_metrics(self, filters, source_version_ids):
        return self.metrics.get_metrics(filters,source_version_ids)

    def retrieve(self, query, filters, top_k, source_version_ids):
        return self.retrieval.retrieve(query,filters,top_k,source_version_ids)

    def get_evidences(self, evidence_ids, source_version_ids):
        """Internal source resolver; not an HTTP or shared contract extension."""
        from contextlib import closing
        from ..contracts.models import Evidence
        if not evidence_ids or not source_version_ids:
            return []
        with closing(self.metrics._connect()) as c,c.cursor() as q:
            q.execute("SELECT e.payload FROM evidences e JOIN document_versions v ON v.document_version_id=e.document_version_id WHERE v.ingest_status='READY' AND e.evidence_id IN ("+','.join(['%s']*len(evidence_ids))+") AND v.document_version_id IN ("+','.join(['%s']*len(source_version_ids))+")",tuple(evidence_ids)+tuple(source_version_ids))
            return [Evidence.model_validate_json(row[0]) for row in q.fetchall()]
