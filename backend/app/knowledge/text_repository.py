"""Atomic text evidence persistence, sharing the W2 evidence tables."""
from contextlib import closing
from pathlib import Path

from ..contracts import models as m
from .repository import ConnectionFactory, VERSION_FIELDS, _record
from .text import parse_text, ParsedText

class TextRepository:
    def __init__(self, connect: ConnectionFactory, runtime_root: Path):
        self._connect = connect
        self._runtime_root = runtime_root

    def ingest_version(self, version_id: str, *, industry: str | None = None) -> ParsedText:
        with closing(self._connect()) as connection:
            connection.begin()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(f"SELECT {VERSION_FIELDS}, original_filename, storage_key "
                                   "FROM document_versions WHERE document_version_id=%s FOR UPDATE", (version_id,))
                    row = cursor.fetchone()
                    if row is None:
                        raise ValueError("unknown document version")
                    parsed = parse_text(self._runtime_root / row[6], row[5], _record(row[:5]))
                    cursor.execute("SELECT e.payload,c.industry FROM evidences e JOIN chunks c ON "
                                   "e.chunk_id=c.chunk_id WHERE e.document_version_id=%s", (version_id,))
                    existing = {}
                    for payload, label in cursor.fetchall():
                        evidence = m.Evidence.model_validate_json(payload)
                        existing[evidence.evidence_id] = (evidence,label)
                    incoming = {e.evidence_id:(e,industry) for e in parsed.evidences}
                    if existing and existing != incoming:
                        raise ValueError("immutable version has different text evidence or metadata")
                    if not existing:
                        for evidence in parsed.evidences:
                            cursor.execute("INSERT INTO chunks VALUES (%s,%s,%s,%s)",
                                           (evidence.chunk_id,version_id,evidence.excerpt,industry))
                            cursor.execute("INSERT INTO evidences VALUES (%s,%s,%s,%s)",
                                           (evidence.evidence_id,evidence.chunk_id,version_id,evidence.model_dump_json()))
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return parsed
