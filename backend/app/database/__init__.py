"""Persistence boundary owned by the backend module.

`store.py` defines the records, the storage protocol and an in-memory
implementation for unit tests and zero-configuration demos. `mysql.py`
implements the same protocol on MySQL via the 020 migration. Domain modules
never touch these tables directly; orchestration and the HTTP layer do.
"""
from .store import (
    ChapterRecord,
    ExportRecord,
    IdempotencyRequest,
    JobRecord,
    MemoryStore,
    ReportRecord,
    new_id,
)
from .mysql import MySqlStore, connect_from_url

__all__ = [
    "ChapterRecord",
    "ExportRecord",
    "IdempotencyRequest",
    "JobRecord",
    "MemoryStore",
    "MySqlStore",
    "ReportRecord",
    "connect_from_url",
    "new_id",
]
