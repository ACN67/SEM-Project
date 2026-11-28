"""Storage records, the shared protocol and the in-memory implementation.

The in-memory store keeps everything in process memory under one lock. It
backs unit tests and the zero-configuration demo (`DATABASE_URL` unset);
production uses `MySqlStore`, which implements the identical surface on the
020_report_orchestration migration.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from threading import Lock
from typing import Protocol
from uuid import uuid4

from ..contracts import models as m
from ..contracts.services import ServiceError

UNSET = object()

_UTC = timezone.utc


def new_id() -> str:
    """Opaque 32-character identifier that fits the CHAR(32) columns."""
    return uuid4().hex


def request_hash(payload: object) -> str:
    """SHA-256 over the UTF-8 JSON of the validated request, sorted keys."""
    return sha256(json_bytes(payload)).hexdigest()


def json_bytes(payload: object) -> bytes:
    return json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")


@dataclass
class ReportRecord:
    report_id: str
    title: str
    report_type: str
    template_id: str
    template_version: int
    latest_job_id: str | None = None
    job_status: str = "QUEUED"
    current_version_id: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class ChapterRecord:
    chapter_id: str
    status: str = "PENDING"
    attempt_id: str | None = None
    retries: int = 0
    content: m.Chapter | None = None
    error: m.ErrorInfo | None = None


@dataclass
class JobRecord:
    job_id: str
    report_id: str
    run_id: str
    previous_job_id: str | None = None
    status: str = "QUEUED"
    stage: str | None = None
    spec: m.Spec | None = None
    frozen: m.FrozenRun | None = None
    # Run-wide evidence/metric packs keyed by ID, persisted for restart-safe retries.
    context: dict | None = None
    result_version_id: str | None = None
    estimated_cost_cny: Decimal = Decimal("0")
    heartbeat_at: datetime | None = None
    error: m.ErrorInfo | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    chapters: list[ChapterRecord] = field(default_factory=list)


@dataclass
class ExportRecord:
    export_id: str
    report_id: str
    version_id: str
    format: str
    status: str = "QUEUED"
    storage_key: str | None = None
    checksum: str | None = None
    size_bytes: int | None = None
    error: m.ErrorInfo | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass
class IdempotencyRequest:
    """A write request guarded by an Idempotency-Key header."""

    operation: str
    target_id: str
    key: str
    hash: str
    report_id: str | None = None


class Store(Protocol):
    """Synchronous storage used by orchestration and the HTTP layer."""

    # -- reports ----------------------------------------------------------
    def create_report(self, report: ReportRecord, now: datetime) -> None: ...
    def get_report(self, report_id: str) -> ReportRecord | None: ...
    def list_reports(
        self, page: int, limit: int
    ) -> tuple[int, list[ReportRecord]]: ...
    def set_report_job(
        self, report_id: str, job_id: str, status: str, now: datetime
    ) -> None: ...
    def set_report_version(
        self, report_id: str, version_id: str, now: datetime
    ) -> None: ...

    # -- jobs -------------------------------------------------------------
    def create_job(self, job: JobRecord, now: datetime) -> None: ...
    def get_job(self, job_id: str) -> JobRecord | None: ...
    def claim_next_queued(self, now: datetime) -> JobRecord | None: ...
    def update_job(
        self,
        job_id: str,
        *,
        status: str | None = None,
        stage: str | None = None,
        frozen: m.FrozenRun | None = None,
        context: dict | None = None,
        result_version_id: str | None = None,
        estimated_cost_cny: Decimal | None = None,
        error: m.ErrorInfo | None = None,
        now: datetime | None = None,
    ) -> None: ...
    def heartbeat_job(self, job_id: str, now: datetime) -> None: ...
    def fail_stale_jobs(self, cutoff: datetime, now: datetime) -> list[str]: ...
    def cancel_job(self, job_id: str, now: datetime) -> JobRecord | None: ...
    def replace_chapters(
        self, job_id: str, chapters: list[ChapterRecord]
    ) -> None: ...
    def update_chapter(
        self,
        job_id: str,
        chapter_id: str,
        *,
        status: str | None = None,
        attempt_id: str | None = None,
        retries: int | None = None,
        content: m.Chapter | None = None,
        error: m.ErrorInfo | None = None,
    ) -> None: ...

    # -- versions ---------------------------------------------------------
    def save_version(
        self, document: m.ReportDocument, *, make_current: bool, now: datetime
    ) -> None: ...
    def get_version(
        self, report_id: str, version_id: str
    ) -> m.ReportDocument | None: ...
    def list_versions(
        self, report_id: str, page: int, limit: int
    ) -> tuple[int, list[m.Version]]: ...

    # -- exports ----------------------------------------------------------
    def begin_export(
        self,
        guard: IdempotencyRequest,
        export: ExportRecord,
        accepted: dict,
        now: datetime,
    ) -> dict:
        """Reserve the idempotency key and register the export atomically.

        Returns the replayed response when the same key and request hash were
        already accepted; raises ServiceError on a conflicting key or body.
        """
    def get_export(self, export_id: str) -> ExportRecord | None: ...
    def claim_next_export(self, now: datetime) -> ExportRecord | None: ...
    def update_export(
        self,
        export_id: str,
        *,
        status: str | None = None,
        storage_key: str | None = None,
        checksum: str | None = None,
        size_bytes: int | None = None,
        error: m.ErrorInfo | None = None,
        now: datetime | None = None,
    ) -> None: ...

    # -- guarded report/job creation --------------------------------------
    def begin_report(
        self,
        guard: IdempotencyRequest,
        report: ReportRecord,
        job: JobRecord,
        accepted: dict,
        now: datetime,
    ) -> dict: ...
    def begin_retry(
        self,
        guard: IdempotencyRequest,
        job: JobRecord,
        accepted: dict,
        now: datetime,
    ) -> dict: ...

    # -- documents (read-only views over knowledge tables) ----------------
    def ready_version_ids(self) -> list[str]: ...
    def queued_ingest_version_ids(self) -> list[str]: ...
    def list_documents(
        self, page: int, limit: int
    ) -> tuple[int, list[m.DocumentRecord]]: ...
    def get_document(self, document_id: str) -> m.DocumentRecord | None: ...
    def get_document_version(
        self, document_id: str, document_version_id: str
    ) -> m.DocumentRecord | None: ...

    # -- admin sessions ----------------------------------------------------
    def create_admin_session(self, session_id: str, expires_at: datetime) -> None: ...
    def admin_session_valid(self, session_id: str, now: datetime) -> bool: ...
    def delete_admin_session(self, session_id: str) -> None: ...


def idempotency_conflict() -> ServiceError:
    return ServiceError(m.ErrorInfo(
        code="IDEMPOTENCY_CONFLICT",
        message="幂等键已被其他请求内容使用，请更换幂等键重试。",
        retryable=False, details={},
    ))


class MemoryStore:
    """Thread-safe in-memory Store for tests and the zero-config demo.

    `connect` mirrors the MySQL constructor so both implementations can be
    built uniformly; the argument is accepted but unused.
    """

    def __init__(self, connect=None) -> None:
        self._lock = Lock()
        self._reports: dict[str, ReportRecord] = {}
        self._jobs: dict[str, JobRecord] = {}
        self._versions: dict[str, m.ReportDocument] = {}
        self._exports: dict[str, ExportRecord] = {}
        self._idempotency: dict[tuple[str, str, str], tuple[str, dict | None]] = {}
        self._documents: dict[str, m.DocumentRecord] = {}
        self._sessions: dict[str, datetime] = {}
        self._sequence = 0
        self._budget_reserved=Decimal(0)

    def _order(self, created_at: datetime | None, key: str) -> tuple:
        """Ascending ordering key: oldest first, ties broken by ID then insertion.

        Listings combine this with ``reverse=True`` (newest first, ID descending
        for equal timestamps per 接口规范); claim helpers use ``min`` for FIFO.
        """
        self._sequence += 1
        stamp = created_at or datetime(1970, 1, 1)
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=_UTC)
        return (stamp.timestamp(), key, self._sequence)

    # -- reports ----------------------------------------------------------
    def create_report(self, report: ReportRecord, now: datetime) -> None:
        with self._lock:
            report.created_at = now
            report.updated_at = now
            self._reports[report.report_id] = replace(report)

    def get_report(self, report_id: str) -> ReportRecord | None:
        with self._lock:
            return replace(self._reports[report_id]) if report_id in self._reports else None

    def list_reports(self, page: int, limit: int) -> tuple[int, list[ReportRecord]]:
        with self._lock:
            records = sorted(
                self._reports.values(),
                key=lambda r: self._order(r.created_at, r.report_id),
                reverse=True,
            )
        start = (page - 1) * limit
        return len(records), [replace(item) for item in records[start:start + limit]]

    def set_report_job(self, report_id: str, job_id: str, status: str, now: datetime) -> None:
        with self._lock:
            report = self._reports[report_id]
            report.latest_job_id = job_id
            report.job_status = status
            report.updated_at = now

    def set_report_version(self, report_id: str, version_id: str, now: datetime) -> None:
        with self._lock:
            report = self._reports[report_id]
            report.current_version_id = version_id
            report.updated_at = now

    # -- jobs -------------------------------------------------------------
    def create_job(self, job: JobRecord, now: datetime) -> None:
        with self._lock:
            job.created_at = now
            job.updated_at = now
            self._jobs[job.job_id] = replace(job)

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            copy = replace(job)
            copy.chapters = [replace(chapter) for chapter in job.chapters]
            return copy

    def claim_next_queued(self, now: datetime) -> JobRecord | None:
        with self._lock:
            queued = [job for job in self._jobs.values() if job.status == "QUEUED"]
            if not queued:
                return None
            job = min(queued, key=lambda item: self._order(item.created_at, item.job_id))
            job.status = "RUNNING"
            job.stage = "PLANNING"
            job.heartbeat_at = now
            job.updated_at = now
            return replace(job)

    def update_job(self, job_id: str, *, status=None, stage=UNSET, frozen=None,
                   context=None, result_version_id=None, estimated_cost_cny=None,
                   error=UNSET, now=None) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if status is not None:
                job.status = status
            if stage is not UNSET:
                job.stage = stage
            if frozen is not None:
                job.frozen = frozen
            if context is not None:
                job.context = context
            if result_version_id is not None:
                job.result_version_id = result_version_id
            if estimated_cost_cny is not None:
                job.estimated_cost_cny = estimated_cost_cny
            if error is not UNSET:
                job.error = error
            if now is not None:
                job.updated_at = now

    def heartbeat_job(self, job_id: str, now: datetime) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.heartbeat_at = now

    def fail_stale_jobs(self, cutoff: datetime, now: datetime) -> list[str]:
        with self._lock:
            lost = [
                job.job_id for job in self._jobs.values()
                if job.status == "RUNNING"
                and job.heartbeat_at is not None
                and job.heartbeat_at < cutoff
            ]
            for job in self._jobs.values():
                if job.job_id in lost:
                    job.status = "FAILED"
                    job.stage = None
                    job.error = m.ErrorInfo(
                        code="WORKER_LOST",
                        message="执行中断，任务已标记为失败；可重试以继续生成。",
                        retryable=False, details={},
                    )
                    job.updated_at = now
            return lost

    def cancel_job(self, job_id: str, now: datetime) -> JobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None or job.status not in ("QUEUED", "RUNNING"):
                return None
            job.status = "CANCELLED"
            job.stage = None
            job.updated_at = now
            return replace(job)

    def replace_chapters(self, job_id: str, chapters: list[ChapterRecord]) -> None:
        with self._lock:
            job = self._jobs[job_id]
            job.chapters = [replace(chapter) for chapter in chapters]

    def queued_ingest_version_ids(self) -> list[str]:
        with self._lock:
            return sorted(
                record.document_version_id for record in self._documents.values()
                if record.ingest_status == "QUEUED"
            )

    def update_chapter(self, job_id: str, chapter_id: str, *, status=None,
                       attempt_id=None, retries=None, content=None, error=UNSET) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.status not in ("QUEUED","RUNNING"):
                return
            for chapter in job.chapters:
                if chapter.chapter_id == chapter_id:
                    if status is not None:
                        chapter.status = status
                    if attempt_id is not None:
                        chapter.attempt_id = attempt_id
                    if retries is not None:
                        chapter.retries = retries
                    if content is not None:
                        chapter.content = content
                    if error is not UNSET:
                        chapter.error = error
                    return
            raise KeyError(chapter_id)

    # -- versions ---------------------------------------------------------
    def save_version(self, document: m.ReportDocument, *, make_current: bool, now: datetime) -> None:
        with self._lock:
            existing = self._versions.get(document.version_id)
            if existing is not None and existing != document:
                raise ValueError("immutable report version conflict")
            self._versions[document.version_id] = document.model_copy(deep=True)
            if make_current:
                report = self._reports[document.report_id]
                report.current_version_id = document.version_id
                report.updated_at = now

    def get_version(self, report_id: str, version_id: str) -> m.ReportDocument | None:
        with self._lock:
            document = self._versions.get(version_id)
            if document is None or document.report_id != report_id:
                return None
            return document.model_copy(deep=True)

    def list_versions(self, report_id: str, page: int, limit: int) -> tuple[int, list[m.Version]]:
        with self._lock:
            documents = [
                document for document in self._versions.values()
                if document.report_id == report_id
            ]
            documents.sort(key=lambda d: self._order(d.created_at, d.version_id), reverse=True)
        versions = [
            m.Version(
                version_id=document.version_id,
                parent_version_id=document.parent_version_id,
                created_at=document.created_at,
                stale_chapter_ids=document.stale_chapter_ids,
                exportable=document.exportable,
            )
            for document in documents
        ]
        start = (page - 1) * limit
        return len(versions), versions[start:start + limit]

    # -- exports ----------------------------------------------------------
    def _begin_guard(self, guard: IdempotencyRequest) -> tuple[str, dict] | None:
        """Return (hash, response) when the key exists, else None."""
        key = (guard.operation, guard.target_id, guard.key)
        if key in self._idempotency:
            stored_hash, response = self._idempotency[key]
            if stored_hash != guard.hash:
                raise idempotency_conflict()
            return stored_hash, response
        return None

    def _store_guard(self, guard: IdempotencyRequest, accepted: dict) -> None:
        self._idempotency[(guard.operation, guard.target_id, guard.key)] = (guard.hash, accepted)

    def begin_export(self, guard: IdempotencyRequest, export: ExportRecord,
                     accepted: dict, now: datetime) -> dict:
        with self._lock:
            replay = self._begin_guard(guard)
            if replay is not None:
                if replay[1] is None:
                    raise idempotency_conflict()
                return replay[1]
            export.created_at = now
            export.updated_at = now
            self._exports[export.export_id] = replace(export)
            self._store_guard(guard, accepted)
            return accepted

    def get_export(self, export_id: str) -> ExportRecord | None:
        with self._lock:
            export = self._exports.get(export_id)
            return replace(export) if export else None

    def claim_next_export(self, now: datetime) -> ExportRecord | None:
        with self._lock:
            queued = [export for export in self._exports.values() if export.status == "QUEUED"]
            if not queued:
                return None
            export = min(queued, key=lambda item: self._order(item.created_at, item.export_id))
            export.status = "RUNNING"
            export.updated_at = now
            return replace(export)

    def update_export(self, export_id: str, *, status=None, storage_key=None,
                      checksum=None, size_bytes=None, error=UNSET, now=None) -> None:
        with self._lock:
            export = self._exports[export_id]
            if status is not None:
                export.status = status
            if storage_key is not None:
                export.storage_key = storage_key
            if checksum is not None:
                export.checksum = checksum
            if size_bytes is not None:
                export.size_bytes = size_bytes
            if error is not UNSET:
                export.error = error
            if now is not None:
                export.updated_at = now

    # -- guarded report/job creation --------------------------------------
    def begin_report(self, guard: IdempotencyRequest, report: ReportRecord,
                     job: JobRecord, accepted: dict, now: datetime) -> dict:
        with self._lock:
            replay = self._begin_guard(guard)
            if replay is not None:
                if replay[1] is None:
                    raise idempotency_conflict()
                return replay[1]
            report.created_at = now
            report.updated_at = now
            self._reports[report.report_id] = replace(report)
            job.created_at = now
            job.updated_at = now
            self._jobs[job.job_id] = replace(job)
            self._store_guard(guard, accepted)
            return accepted

    def begin_retry(self, guard: IdempotencyRequest, job: JobRecord,
                    accepted: dict, now: datetime) -> dict:
        with self._lock:
            replay = self._begin_guard(guard)
            if replay is not None:
                if replay[1] is None:
                    raise idempotency_conflict()
                return replay[1]
            job.created_at = now
            job.updated_at = now
            self._jobs[job.job_id] = replace(job)
            report=self._reports[job.report_id]
            report.latest_job_id=job.job_id
            report.job_status='QUEUED'
            report.updated_at=now
            self._store_guard(guard, accepted)
            return accepted

    # -- documents --------------------------------------------------------
    def seed_document(self, record: m.DocumentRecord) -> None:
        """Register a document record directly; used by tests and demos."""
        with self._lock:
            self._documents[record.document_id] = record

    def ready_version_ids(self) -> list[str]:
        with self._lock:
            return sorted(
                record.document_version_id for record in self._documents.values()
                if record.ingest_status == "READY"
            )

    def list_documents(self, page: int, limit: int) -> tuple[int, list[m.DocumentRecord]]:
        with self._lock:
            records = sorted(
                self._documents.values(),
                key=lambda r: r.document_id,
                reverse=True,
            )
        start = (page - 1) * limit
        return len(records), [record.model_copy(deep=True) for record in records[start:start + limit]]

    def get_document(self, document_id: str) -> m.DocumentRecord | None:
        with self._lock:
            record = self._documents.get(document_id)
            return record.model_copy(deep=True) if record else None

    def get_document_version(self, document_id: str, document_version_id: str) -> m.DocumentRecord | None:
        with self._lock:
            record = self._documents.get(document_id)
            if record and record.document_version_id == document_version_id:
                return record.model_copy(deep=True)
            return None

    # -- admin sessions ----------------------------------------------------
    def create_admin_session(self, session_id: str, expires_at: datetime) -> None:
        with self._lock:
            self._sessions[session_id] = expires_at

    def admin_session_valid(self, session_id: str, now: datetime) -> bool:
        with self._lock:
            expires = self._sessions.get(session_id)
            return expires is not None and expires > now

    def delete_admin_session(self, session_id: str) -> None:
        with self._lock:
            self._sessions.pop(session_id, None)

    def reserve_budget(self, amount, limit):
        if not amount.is_finite() or not limit.is_finite() or amount<=0 or limit<0:
            raise ValueError("invalid budget reservation")
        with self._lock:
            if self._budget_reserved+amount>limit:
                return False
            self._budget_reserved+=amount
            return True

    def finish_attempt(self, job_id, chapter_id, attempt_id, *, content=None, error=None, frozen=None, context=None):
        with self._lock:
            job=self._jobs[job_id]
            chapter=next(c for c in job.chapters if c.chapter_id==chapter_id)
            if job.status!='RUNNING' or chapter.status!='RUNNING' or chapter.attempt_id!=attempt_id:
                return False
            chapter.status='SUCCEEDED' if error is None else 'FAILED'
            chapter.content=content
            chapter.error=error
            if frozen is not None:job.frozen=frozen
            if context is not None:job.context=context
            return True
