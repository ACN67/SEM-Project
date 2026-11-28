"""MySQL implementation of the Store protocol on migration 020.

Connection URLs follow `mysql://user:password@host:port/database`. Timestamps
are written as naive UTC and read back as UTC-aware values so the contract's
`AwareDatetime` fields stay consistent. Each mutating method is atomic; the
guarded creation methods run the idempotency check, resource insert and
accepted response in one transaction, matching 接口规范 v1.0.
"""
from __future__ import annotations

import json
from hashlib import sha256
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import urlsplit

import pymysql
import pymysql.cursors
from pydantic import TypeAdapter

from ..contracts import models as m
from ..contracts.services import ServiceError
from .store import (
    UNSET,
    ChapterRecord,
    ExportRecord,
    IdempotencyRequest,
    JobRecord,
    ReportRecord,
    idempotency_conflict,
)

_UTC = timezone.utc
_JOB_STATUSES = ("QUEUED", "RUNNING", "COMPLETED", "COMPLETED_WITH_WARNINGS", "FAILED", "CANCELLED")


def connect_from_url(url: str):
    """Return a connection factory that opens a PyMySQL connection per call.

    The store opens short-lived connections per operation, matching the
    knowledge module's `connect` parameter convention.
    """
    parsed = urlsplit(url.strip())
    if parsed.scheme not in ("mysql", "mariadb", "mysql+pymysql") or not parsed.hostname:
        raise ValueError("DATABASE_URL 必须是 mysql://user:password@host:port/database 形式。")

    def connect():
        return pymysql.connect(
            host=parsed.hostname,
            port=parsed.port or 3306,
            user=parsed.username or "root",
            password=parsed.password or "",
            database=(parsed.path or "/").lstrip("/") or None,
            charset="utf8mb4",
            autocommit=True,
            cursorclass=pymysql.cursors.DictCursor,
        )

    return connect


def _naive(value: datetime) -> datetime:
    if value.tzinfo is not None:
        return value.astimezone(_UTC).replace(tzinfo=None)
    return value


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=_UTC)
    return value.astimezone(_UTC)


def _dump(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, m.Model):
        return value.model_dump_json(by_alias=True)
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _error(value) -> m.ErrorInfo | None:
    if value is None:
        return None
    if isinstance(value, str):
        return m.ErrorInfo.model_validate_json(value)
    return m.ErrorInfo.model_validate(value)


def _cost(value) -> Decimal:
    return Decimal(str(value or 0))


class MySqlStore:
    """Store implementation backed by the 020_report_orchestration tables."""

    def __init__(self, connect, *, document_connect=None):
        self._connect = connect
        # Document tables belong to the knowledge migration; a separate pool
        # can be injected when the report database and knowledge database differ.
        self._document_connect = document_connect or connect

    # -- reports ----------------------------------------------------------
    def create_report(self, report: ReportRecord, now: datetime) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO reports (report_id, title, report_type, template_id, template_version,"
                " latest_job_id, job_status, current_version_id, created_at, updated_at)"
                " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (report.report_id, report.title, report.report_type, report.template_id,
                 report.template_version, report.latest_job_id, report.job_status,
                 report.current_version_id, _naive(now), _naive(now)),
            )

    def _row_to_report(self, row) -> ReportRecord:
        return ReportRecord(
            report_id=row["report_id"], title=row["title"], report_type=row["report_type"],
            template_id=row["template_id"], template_version=row["template_version"],
            latest_job_id=row["latest_job_id"], job_status=row["job_status"],
            current_version_id=row["current_version_id"],
            created_at=_aware(row["created_at"]), updated_at=_aware(row["updated_at"]),
        )

    def get_report(self, report_id: str) -> ReportRecord | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM reports WHERE report_id=%s", (report_id,))
            row = cursor.fetchone()
            return self._row_to_report(row) if row else None

    def list_reports(self, page: int, limit: int) -> tuple[int, list[ReportRecord]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT COUNT(*) AS total FROM reports")
            total = cursor.fetchone()["total"]
            cursor.execute(
                "SELECT * FROM reports ORDER BY created_at DESC, report_id DESC LIMIT %s OFFSET %s",
                (limit, (page - 1) * limit),
            )
            return total, [self._row_to_report(row) for row in cursor.fetchall()]

    def set_report_job(self, report_id: str, job_id: str, status: str, now: datetime) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE reports SET latest_job_id=%s, job_status=%s, updated_at=%s WHERE report_id=%s",
                (job_id, status, _naive(now), report_id),
            )

    def set_report_version(self, report_id: str, version_id: str, now: datetime) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE reports SET current_version_id=%s, updated_at=%s WHERE report_id=%s",
                (version_id, _naive(now), report_id),
            )

    # -- jobs -------------------------------------------------------------
    def _insert_job(self, cursor, job: JobRecord, now: datetime) -> None:
        cursor.execute(
            "INSERT INTO jobs (job_id, report_id, run_id, previous_job_id, status, stage,"
            " spec_json, frozen_json, context_json, result_version_id, estimated_cost_cny,"
            " heartbeat_at, error_json, created_at, updated_at)"
            " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (job.job_id, job.report_id, job.run_id, job.previous_job_id, job.status, job.stage,
             _dump(job.spec), _dump(job.frozen), _dump(job.context), job.result_version_id,
             str(job.estimated_cost_cny),
             _naive(job.heartbeat_at) if job.heartbeat_at else None,
             _dump(job.error), _naive(now), _naive(now)),
        )
        for chapter in job.chapters:
            self._insert_chapter(cursor, job.job_id, chapter)

    @staticmethod
    def _insert_chapter(cursor, job_id: str, chapter: ChapterRecord) -> None:
        cursor.execute(
            "INSERT INTO chapter_runs (job_id, chapter_id, status, attempt_id, retries,"
            " content_json, error_json) VALUES (%s,%s,%s,%s,%s,%s,%s)",
            (job_id, chapter.chapter_id, chapter.status, chapter.attempt_id, chapter.retries,
             _dump(chapter.content), _dump(chapter.error)),
        )

    def create_job(self, job: JobRecord, now: datetime) -> None:
        with self._connect() as connection:
            connection.begin()
            try:
                with connection.cursor() as cursor:
                    self._insert_job(cursor, job, now)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    @staticmethod
    def _row_to_chapter(row) -> ChapterRecord:
        content = None
        if row["content_json"] is not None:
            content = m.Chapter.model_validate_json(row["content_json"])
        return ChapterRecord(
            chapter_id=row["chapter_id"], status=row["status"], attempt_id=row["attempt_id"],
            retries=row["retries"], content=content, error=_error(row["error_json"]),
        )

    def _row_to_job(self, row, chapters) -> JobRecord:
        spec = None
        if row["spec_json"] is not None:
            spec = TypeAdapter(m.Spec).validate_json(row["spec_json"])
        frozen = None
        if row["frozen_json"] is not None:
            frozen = m.FrozenRun.model_validate_json(row["frozen_json"])
        context = None
        if row["context_json"] is not None:
            context = json.loads(row["context_json"])
        return JobRecord(
            job_id=row["job_id"], report_id=row["report_id"], run_id=row["run_id"],
            previous_job_id=row["previous_job_id"], status=row["status"], stage=row["stage"],
            spec=spec, frozen=frozen, context=context,
            result_version_id=row["result_version_id"],
            estimated_cost_cny=_cost(row["estimated_cost_cny"]),
            heartbeat_at=_aware(row["heartbeat_at"]), error=_error(row["error_json"]),
            created_at=_aware(row["created_at"]), updated_at=_aware(row["updated_at"]),
            chapters=chapters,
        )

    def get_job(self, job_id: str) -> JobRecord | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM jobs WHERE job_id=%s", (job_id,))
            row = cursor.fetchone()
            if row is None:
                return None
            cursor.execute(
                "SELECT * FROM chapter_runs WHERE job_id=%s ORDER BY chapter_id", (job_id,),
            )
            chapters = [self._row_to_chapter(chapter) for chapter in cursor.fetchall()]
            return self._row_to_job(row, chapters)

    def claim_next_queued(self, now: datetime) -> JobRecord | None:
        with self._connect() as connection:
            connection.begin()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT job_id FROM jobs WHERE status='QUEUED'"
                        " ORDER BY created_at, job_id LIMIT 1 FOR UPDATE",
                    )
                    row = cursor.fetchone()
                    if row is None:
                        connection.rollback()
                        return None
                    job_id = row["job_id"]
                    cursor.execute(
                        "UPDATE jobs SET status='RUNNING', stage='PLANNING', heartbeat_at=%s,"
                        " updated_at=%s WHERE job_id=%s AND status='QUEUED'",
                        (_naive(now), _naive(now), job_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return self.get_job(job_id)

    def update_job(self, job_id: str, *, status=None, stage=UNSET, frozen=None,
                   context=None, result_version_id=None, estimated_cost_cny=None,
                   error=UNSET, now=None) -> None:
        assignments = []
        params: list[object] = []
        if status is not None:
            if status not in _JOB_STATUSES:
                raise ValueError(f"unknown job status {status}")
            assignments.append("status=%s")
            params.append(status)
        if stage is not UNSET:
            assignments.append("stage=%s")
            params.append(stage)
        if frozen is not None:
            assignments.append("frozen_json=%s")
            params.append(_dump(frozen))
        if context is not None:
            assignments.append("context_json=%s")
            params.append(_dump(context))
        if result_version_id is not None:
            assignments.append("result_version_id=%s")
            params.append(result_version_id)
        if estimated_cost_cny is not None:
            assignments.append("estimated_cost_cny=%s")
            params.append(str(estimated_cost_cny))
        if error is not UNSET:
            assignments.append("error_json=%s")
            params.append(_dump(error))
        if now is not None:
            assignments.append("updated_at=%s")
            params.append(_naive(now))
        if not assignments:
            return
        params.append(job_id)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"UPDATE jobs SET {', '.join(assignments)} WHERE job_id=%s", params)

    def heartbeat_job(self, job_id: str, now: datetime) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE jobs SET heartbeat_at=%s, updated_at=%s WHERE job_id=%s",
                (_naive(now), _naive(now), job_id),
            )

    def fail_stale_jobs(self, cutoff: datetime, now: datetime) -> list[str]:
        lost = m.ErrorInfo(
            code="WORKER_LOST",
            message="执行中断，任务已标记为失败；可重试以继续生成。",
            retryable=False, details={},
        )
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT job_id FROM jobs WHERE status='RUNNING'"
                " AND heartbeat_at IS NOT NULL AND heartbeat_at < %s",
                (_naive(cutoff),),
            )
            jobs = [row["job_id"] for row in cursor.fetchall()]
            if jobs:
                cursor.execute(
                    "UPDATE jobs SET status='FAILED', stage=NULL, error_json=%s, updated_at=%s"
                    " WHERE status='RUNNING' AND heartbeat_at IS NOT NULL AND heartbeat_at < %s",
                    (_dump(lost), _naive(now), _naive(cutoff)),
                )
            return jobs

    def cancel_job(self, job_id: str, now: datetime) -> JobRecord | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "UPDATE jobs SET status='CANCELLED', stage=NULL, updated_at=%s"
                " WHERE job_id=%s AND status IN ('QUEUED','RUNNING')",
                (_naive(now), job_id),
            )
            if cursor.rowcount == 0:
                return None
        return self.get_job(job_id)

    def update_chapter(self, job_id: str, chapter_id: str, *, status=None,
                       attempt_id=None, retries=None, content=None, error=UNSET) -> None:
        assignments = []
        params: list[object] = []
        if status is not None:
            assignments.append("status=%s")
            params.append(status)
        if attempt_id is not None:
            assignments.append("attempt_id=%s")
            params.append(attempt_id)
        if retries is not None:
            assignments.append("retries=%s")
            params.append(retries)
        if content is not None:
            assignments.append("content_json=%s")
            params.append(_dump(content))
        if error is not UNSET:
            assignments.append("error_json=%s")
            params.append(_dump(error))
        if not assignments:
            return
        params.extend([job_id, chapter_id,job_id])
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                f"UPDATE chapter_runs SET {', '.join(assignments)}"
                " WHERE job_id=%s AND chapter_id=%s AND job_id IN (SELECT job_id FROM jobs WHERE job_id=%s AND status IN ('QUEUED','RUNNING'))",
                params,
            )

    def replace_chapters(self, job_id: str, chapters: list[ChapterRecord]) -> None:
        with self._connect() as connection:
            connection.begin()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("DELETE FROM chapter_runs WHERE job_id=%s", (job_id,))
                    for chapter in chapters:
                        self._insert_chapter(cursor, job_id, chapter)
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def queued_ingest_version_ids(self) -> list[str]:
        with self._document_connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT document_version_id FROM document_versions"
                " WHERE ingest_status='QUEUED' ORDER BY created_at"
            )
            return [row["document_version_id"] for row in cursor.fetchall()]

    # -- versions ---------------------------------------------------------
    def save_version(self, document: m.ReportDocument, *, make_current: bool, now: datetime) -> None:
        with self._connect() as connection:
            connection.begin()
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT document_json FROM report_versions WHERE version_id=%s FOR UPDATE", (document.version_id,))
                    existing=cursor.fetchone()
                    if existing:
                        if m.ReportDocument.model_validate_json(existing['document_json']) != document:
                            raise ValueError("immutable report version conflict")
                        if make_current:
                            cursor.execute("UPDATE reports SET current_version_id=%s,updated_at=%s WHERE report_id=%s", (document.version_id,_naive(now),document.report_id))
                        connection.commit()
                        return
                    cursor.execute(
                        "INSERT INTO report_versions (report_id, version_id, parent_version_id,"
                        " document_json, exportable, stale_chapter_ids, created_at)"
                        " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                        (document.report_id, document.version_id, document.parent_version_id,
                         document.model_dump_json(by_alias=True), document.exportable,
                         _dump(document.stale_chapter_ids), _naive(now)),
                    )
                    if make_current:
                        cursor.execute(
                            "UPDATE reports SET current_version_id=%s, updated_at=%s"
                            " WHERE report_id=%s",
                            (document.version_id, _naive(now), document.report_id),
                        )
                connection.commit()
            except Exception:
                connection.rollback()
                raise

    def get_version(self, report_id: str, version_id: str) -> m.ReportDocument | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT document_json FROM report_versions WHERE report_id=%s AND version_id=%s",
                (report_id, version_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            return m.ReportDocument.model_validate_json(row["document_json"])

    def list_versions(self, report_id: str, page: int, limit: int) -> tuple[int, list[m.Version]]:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS total FROM report_versions WHERE report_id=%s",
                (report_id,),
            )
            total = cursor.fetchone()["total"]
            cursor.execute(
                "SELECT version_id, parent_version_id, created_at, exportable, stale_chapter_ids"
                " FROM report_versions WHERE report_id=%s"
                " ORDER BY created_at DESC, version_id DESC LIMIT %s OFFSET %s",
                (report_id, limit, (page - 1) * limit),
            )
            versions = [
                m.Version(
                    version_id=row["version_id"], parent_version_id=row["parent_version_id"],
                    created_at=_aware(row["created_at"]), exportable=bool(row["exportable"]),
                    stale_chapter_ids=json.loads(row["stale_chapter_ids"] or "[]"),
                )
                for row in cursor.fetchall()
            ]
            return total, versions

    # -- exports ----------------------------------------------------------
    def _guarded(self, cursor, guard: IdempotencyRequest) -> dict | None:
        lock_name=sha256((guard.operation+'|'+guard.target_id+'|'+guard.key).encode()).hexdigest()
        cursor.execute("SELECT GET_LOCK(%s,10) AS acquired", (lock_name,))
        if cursor.fetchone()['acquired'] != 1:
            raise RuntimeError("idempotency lock timeout")
        cursor.execute(
            "SELECT request_hash, response_json FROM idempotency_keys"
            " WHERE operation=%s AND target_id=%s AND idempotency_key=%s FOR UPDATE",
            (guard.operation, guard.target_id, guard.key),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        if row["request_hash"] != guard.hash or row["response_json"] is None:
            raise idempotency_conflict()
        return json.loads(row["response_json"])

    @staticmethod
    def _store_guard(cursor, guard: IdempotencyRequest, accepted: dict) -> None:
        cursor.execute(
            "INSERT INTO idempotency_keys (operation, target_id, idempotency_key, request_hash,"
            " report_id, response_json) VALUES (%s,%s,%s,%s,%s,%s)",
            (guard.operation, guard.target_id, guard.key, guard.hash, guard.report_id,
             json.dumps(accepted, ensure_ascii=False, separators=(",", ":"))),
        )

    def begin_export(self, guard: IdempotencyRequest, export: ExportRecord,
                     accepted: dict, now: datetime) -> dict:
        with self._connect() as connection:
            connection.begin()
            try:
                with connection.cursor() as cursor:
                    replay = self._guarded(cursor, guard)
                    if replay is not None:
                        connection.commit()
                        return replay
                    cursor.execute(
                        "INSERT INTO exports (export_id, report_id, version_id, format, status,"
                        " created_at, updated_at) VALUES (%s,%s,%s,%s,'QUEUED',%s,%s)",
                        (export.export_id, export.report_id, export.version_id, export.format,
                         _naive(now), _naive(now)),
                    )
                    self._store_guard(cursor, guard, accepted)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return accepted

    @staticmethod
    def _row_to_export(row) -> ExportRecord:
        return ExportRecord(
            export_id=row["export_id"], report_id=row["report_id"],
            version_id=row["version_id"], format=row["format"], status=row["status"],
            storage_key=row["storage_key"], checksum=row["checksum"],
            size_bytes=row["size_bytes"], error=_error(row["error_json"]),
            created_at=_aware(row["created_at"]), updated_at=_aware(row["updated_at"]),
        )

    def get_export(self, export_id: str) -> ExportRecord | None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("SELECT * FROM exports WHERE export_id=%s", (export_id,))
            row = cursor.fetchone()
            return self._row_to_export(row) if row else None

    def claim_next_export(self, now: datetime) -> ExportRecord | None:
        with self._connect() as connection:
            connection.begin()
            try:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT export_id FROM exports WHERE status='QUEUED'"
                        " ORDER BY created_at, export_id LIMIT 1 FOR UPDATE",
                    )
                    row = cursor.fetchone()
                    if row is None:
                        connection.rollback()
                        return None
                    export_id = row["export_id"]
                    cursor.execute(
                        "UPDATE exports SET status='RUNNING', updated_at=%s"
                        " WHERE export_id=%s AND status='QUEUED'",
                        (_naive(now), export_id),
                    )
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return self.get_export(export_id)

    def update_export(self, export_id: str, *, status=None, storage_key=None,
                      checksum=None, size_bytes=None, error=UNSET, now=None) -> None:
        assignments = []
        params: list[object] = []
        if status is not None:
            assignments.append("status=%s")
            params.append(status)
        if storage_key is not None:
            assignments.append("storage_key=%s")
            params.append(storage_key)
        if checksum is not None:
            assignments.append("checksum=%s")
            params.append(checksum)
        if size_bytes is not None:
            assignments.append("size_bytes=%s")
            params.append(size_bytes)
        if error is not UNSET:
            assignments.append("error_json=%s")
            params.append(_dump(error))
        if now is not None:
            assignments.append("updated_at=%s")
            params.append(_naive(now))
        if not assignments:
            return
        params.append(export_id)
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(f"UPDATE exports SET {', '.join(assignments)} WHERE export_id=%s", params)

    # -- guarded report/job creation --------------------------------------
    def begin_report(self, guard: IdempotencyRequest, report: ReportRecord,
                     job: JobRecord, accepted: dict, now: datetime) -> dict:
        with self._connect() as connection:
            connection.begin()
            try:
                with connection.cursor() as cursor:
                    replay = self._guarded(cursor, guard)
                    if replay is not None:
                        connection.commit()
                        return replay
                    # Same transaction as the job and the idempotency record.
                    cursor.execute(
                        "INSERT INTO reports (report_id, title, report_type, template_id,"
                        " template_version, latest_job_id, job_status, current_version_id,"
                        " created_at, updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (report.report_id, report.title, report.report_type, report.template_id,
                         report.template_version, job.job_id, job.status, None,
                         _naive(now), _naive(now)),
                    )
                    self._insert_job(cursor, job, now)
                    self._store_guard(cursor, guard, accepted)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return accepted

    def begin_retry(self, guard: IdempotencyRequest, job: JobRecord,
                    accepted: dict, now: datetime) -> dict:
        with self._connect() as connection:
            connection.begin()
            try:
                with connection.cursor() as cursor:
                    replay = self._guarded(cursor, guard)
                    if replay is not None:
                        connection.commit()
                        return replay
                    self._insert_job(cursor, job, now)
                    cursor.execute("UPDATE reports SET latest_job_id=%s,job_status='QUEUED',updated_at=%s WHERE report_id=%s",(job.job_id,_naive(now),job.report_id))
                    self._store_guard(cursor, guard, accepted)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
            return accepted

    # -- documents --------------------------------------------------------
    def ready_version_ids(self) -> list[str]:
        with self._document_connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT v.document_version_id FROM document_versions v"
                " WHERE v.ingest_status='READY' AND NOT EXISTS (SELECT 1 FROM document_versions newer WHERE newer.document_id=v.document_id AND newer.ingest_status='READY' AND newer.registration_order>v.registration_order) ORDER BY v.document_version_id",
            )
            return [row["document_version_id"] for row in cursor.fetchall()]

    def _document_record(self, row) -> m.DocumentRecord:
        return m.DocumentRecord.model_validate({
            "document_id": row["document_id"],
            "document_version_id": row["document_version_id"],
            "ingest_status": row["ingest_status"],
            "error": _error(row["error_json"]),
            **json.loads(row["metadata_json"]),
        })

    def list_documents(self, page: int, limit: int) -> tuple[int, list[m.DocumentRecord]]:
        with self._document_connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT COUNT(*) AS total FROM document_versions"
                " JOIN documents USING (document_id)"
            )
            total = cursor.fetchone()["total"]
            cursor.execute(
                "SELECT document_id, document_version_id, ingest_status, error_json, metadata_json"
                " FROM document_versions JOIN documents USING (document_id)"
                " ORDER BY created_at DESC, document_version_id DESC LIMIT %s OFFSET %s",
                (limit, (page - 1) * limit),
            )
            return total, [self._document_record(row) for row in cursor.fetchall()]

    def get_document(self, document_id: str) -> m.DocumentRecord | None:
        with self._document_connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT document_id, document_version_id, ingest_status, error_json, metadata_json"
                " FROM document_versions JOIN documents USING (document_id)"
                " WHERE document_versions.document_id=%s"
                " ORDER BY registration_order DESC LIMIT 1",
                (document_id,),
            )
            row = cursor.fetchone()
            return self._document_record(row) if row else None

    def get_document_version(
        self, document_id: str, document_version_id: str
    ) -> m.DocumentRecord | None:
        with self._document_connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT document_id, document_version_id, ingest_status, error_json, metadata_json"
                " FROM document_versions JOIN documents USING (document_id)"
                " WHERE document_versions.document_id=%s AND document_version_id=%s",
                (document_id, document_version_id),
            )
            row = cursor.fetchone()
            return self._document_record(row) if row else None

    # -- admin sessions ----------------------------------------------------
    def create_admin_session(self, session_id: str, expires_at: datetime) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO admin_sessions (session_id, expires_at) VALUES (%s, %s)",
                (session_id, _naive(expires_at)),
            )

    def admin_session_valid(self, session_id: str, now: datetime) -> bool:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute(
                "SELECT 1 FROM admin_sessions WHERE session_id=%s AND expires_at > %s",
                (session_id, _naive(now)),
            )
            return cursor.fetchone() is not None

    def delete_admin_session(self, session_id: str) -> None:
        with self._connect() as connection, connection.cursor() as cursor:
            cursor.execute("DELETE FROM admin_sessions WHERE session_id=%s", (session_id,))

    def reserve_budget(self, amount, limit):
        if not amount.is_finite() or not limit.is_finite() or amount<=0 or limit<0:
            raise ValueError("invalid budget reservation")
        with self._connect() as c:
            c.begin()
            try:
                with c.cursor() as q:
                    q.execute("INSERT IGNORE INTO project_budget VALUES (1,0)")
                    q.execute("SELECT reserved FROM project_budget WHERE id=1 FOR UPDATE")
                    value=Decimal(q.fetchone()['reserved'])+amount
                    if value>limit:
                        c.rollback()
                        return False
                    q.execute("UPDATE project_budget SET reserved=%s WHERE id=1", (str(value),))
                c.commit()
                return True
            except Exception:
                c.rollback()
                raise

    def finish_attempt(self, job_id, chapter_id, attempt_id, *, content=None, error=None, frozen=None, context=None):
        with self._connect() as c:
            c.begin()
            try:
                with c.cursor() as q:
                    q.execute("SELECT status FROM jobs WHERE job_id=%s FOR UPDATE",(job_id,))
                    if q.fetchone()['status']!='RUNNING':
                        c.rollback();return False
                    q.execute("UPDATE chapter_runs SET status=%s,content_json=%s,error_json=%s WHERE job_id=%s AND chapter_id=%s AND status='RUNNING' AND attempt_id=%s",('SUCCEEDED' if error is None else 'FAILED',_dump(content),_dump(error),job_id,chapter_id,attempt_id))
                    if q.rowcount!=1:
                        c.rollback();return False
                    if frozen is not None:q.execute("UPDATE jobs SET frozen_json=%s,context_json=%s WHERE job_id=%s",(_dump(frozen),_dump(context),job_id))
                c.commit();return True
            except Exception:
                c.rollback();raise
