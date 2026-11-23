"""W1 upload intake. Parsing and indexing are scheduled separately."""
from pathlib import Path

from ..contracts import models as m
from .repository import DocumentRepository
from .storage import OriginalStorage, filename_key


class DocumentIngest:
    def __init__(self, storage: OriginalStorage, repository: DocumentRepository):
        self._storage = storage
        self._repository = repository

    def ingest_document(self, file: Path, metadata: m.Metadata) -> m.IngestAccepted:
        name_key = filename_key(file.name)
        checksum, storage_key = self._storage.persist(file)
        record = self._repository.register(file.name, name_key, checksum, storage_key, metadata)
        return m.IngestAccepted(
            document_id=record.document_id,
            document_version_id=record.document_version_id,
            ingest_status=record.ingest_status,
            status_url=f"/api/v1/documents/{record.document_id}/versions/{record.document_version_id}",
        )
