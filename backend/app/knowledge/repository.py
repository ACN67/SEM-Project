"""MySQL persistence for immutable originals and their mutable ingest status."""
from collections.abc import Callable
from contextlib import closing
from uuid import uuid4

from pymysql import Connection, MySQLError

from ..contracts import models as m
from .storage import ingest_error

ConnectionFactory = Callable[[], Connection]
VERSION_FIELDS = "document_id, document_version_id, ingest_status, metadata_json, error_json"
RETRYABLE_CONFLICTS = {1062, 1205, 1213}


def _record(row: tuple) -> m.DocumentRecord:
    document_id, version, status, metadata, error = row
    return m.DocumentRecord(
        **m.Metadata.model_validate_json(metadata).model_dump(),
        document_id=document_id, document_version_id=version, ingest_status=status,
        error=m.ErrorInfo.model_validate_json(error) if error is not None else None,
    )


class DocumentRepository:
    def __init__(self, connect: ConnectionFactory):
        self._connect = connect

    def register(
        self, filename: str, name_key: bytes, checksum: str, storage_key: str, metadata: m.Metadata,
    ) -> m.DocumentRecord:
        for attempt in range(4):
            try:
                return self._register_once(filename, name_key, checksum, storage_key, metadata)
            except MySQLError as exc:
                conflict = bool(exc.args and exc.args[0] in RETRYABLE_CONFLICTS)
                if not conflict or attempt == 3:
                    raise ingest_error(
                        "DOCUMENT_STORAGE_ERROR", "资料版本登记失败。", retryable=True,
                    ) from exc
        raise AssertionError("unreachable")

    def _register_once(
        self, filename: str, name_key: bytes, checksum: str, storage_key: str, metadata: m.Metadata,
    ) -> m.DocumentRecord:
        with closing(self._connect()) as connection:
            connection.begin()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        f"SELECT {VERSION_FIELDS} FROM document_versions WHERE sha256 = %s",
                        (checksum,),
                    )
                    duplicate = cursor.fetchone()
                    if duplicate is not None:
                        record = _record(duplicate)
                        cursor.execute(
                            "INSERT INTO document_filenames (filename_key, document_id) VALUES (%s, %s) "
                            "ON DUPLICATE KEY UPDATE filename_key = filename_key",
                            (name_key, record.document_id),
                        )
                    else:
                        cursor.execute(
                            "SELECT document_id FROM document_filenames WHERE filename_key = %s", (name_key,),
                        )
                        existing = cursor.fetchone()
                        if existing is None:
                            document_id = uuid4().hex
                            cursor.execute("INSERT INTO documents (document_id) VALUES (%s)", (document_id,))
                            cursor.execute(
                                "INSERT INTO document_filenames (filename_key, document_id) VALUES (%s, %s)",
                                (name_key, document_id),
                            )
                        else:
                            document_id = existing[0]
                        # Serialize writes to existing documents so their order reflects registration.
                        cursor.execute("SELECT document_id FROM documents WHERE document_id = %s FOR UPDATE",
                                       (document_id,))
                        version = uuid4().hex
                        cursor.execute(
                            "INSERT INTO document_versions "
                            "(document_id, document_version_id, sha256, original_filename, storage_key, metadata_json) "
                            "VALUES (%s, %s, %s, %s, %s, %s)",
                            (document_id, version, checksum, filename, storage_key, metadata.model_dump_json()),
                        )
                        record = m.DocumentRecord(
                            **metadata.model_dump(), document_id=document_id,
                            document_version_id=version, ingest_status="QUEUED", error=None,
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return record

    def get_version(self, document_id: str, version_id: str) -> m.DocumentRecord | None:
        return self._get(
            "WHERE document_id = %s AND document_version_id = %s", (document_id, version_id),
        )

    def get_latest(self, document_id: str) -> m.DocumentRecord | None:
        return self._get("WHERE document_id = %s ORDER BY registration_order DESC LIMIT 1", (document_id,))

    def _get(self, clause: str, params: tuple[str, ...]) -> m.DocumentRecord | None:
        try:
            with closing(self._connect()) as connection, connection.cursor() as cursor:
                cursor.execute(f"SELECT {VERSION_FIELDS} FROM document_versions {clause}", params)
                row = cursor.fetchone()
        except MySQLError as exc:
            raise ingest_error("DOCUMENT_STORAGE_ERROR", "资料版本读取失败。", retryable=True) from exc
        return _record(row) if row is not None else None
