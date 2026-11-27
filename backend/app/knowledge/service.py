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
