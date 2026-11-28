"""HTTP routes implementing 接口规范 v1.0 (chapter editing deferred to R12)."""
from __future__ import annotations

import hmac
import secrets
import tempfile
from datetime import timedelta
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Body, Form, Header, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse

from ..contracts import models as m
from ..contracts.services import ServiceError

COOKIE_NAME = "sem_admin"
SESSION_TTL = timedelta(hours=8)


class AppServices:
    """Wiring shared by all routes; built in backend.app.main.create_app."""

    def __init__(self, *, store, catalog, knowledge, orchestration, settings):
        self.store = store
        self.catalog = catalog
        self.knowledge = knowledge
        self.orchestration = orchestration
        self.settings = settings

    def now(self) -> datetime:
        return self.orchestration.clock()


def _services(request: Request) -> AppServices:
    return request.app.state.services


def _require_admin(request: Request) -> None:
    services = _services(request)
    session = request.cookies.get(COOKIE_NAME)
    if not session or not services.store.admin_session_valid(session, services.now()):
        raise ServiceError(m.ErrorInfo(
            code="AUTH_REQUIRED", message="需要管理员会话。", retryable=False, details={},
        ))


def _check_origin(request: Request) -> None:
    """Reject browser-originated admin writes whose Origin mismatches the host."""
    origin = request.headers.get("origin")
    if not origin:
        return  # non-browser clients (curl, tests) legitimately omit Origin
    host = request.headers.get("host")
    if host and origin.rsplit("//", 1)[-1] != host:
        raise ServiceError(m.ErrorInfo(
            code="AUTH_REQUIRED", message="请求来源校验失败。", retryable=False, details={},
        ))


admin_router = APIRouter()
templates_router = APIRouter()
documents_router = APIRouter()
reports_router = APIRouter()
jobs_router = APIRouter()
exports_router = APIRouter()


# ---------------------------------------------------------------------------
# Admin sessions
# ---------------------------------------------------------------------------
@admin_router.post("/admin/session", status_code=204)
def create_admin_session(request: Request, login: m.Login) -> Response:
    services = _services(request)
    _check_origin(request)
    expected = services.settings.admin_credential
    if not expected or not hmac.compare_digest(login.credential, expected):
        raise ServiceError(m.ErrorInfo(
            code="AUTH_REQUIRED", message="管理凭据无效。", retryable=False, details={},
        ))
    session_id = secrets.token_hex(32)
    services.store.create_admin_session(session_id, services.now() + SESSION_TTL)
    response = Response(status_code=204)
    response.set_cookie(
        COOKIE_NAME, session_id, max_age=int(SESSION_TTL.total_seconds()),
        httponly=True, samesite="strict",
    )
    return response


@admin_router.delete("/admin/session", status_code=204)
def delete_admin_session(request: Request) -> Response:
    _require_admin(request)
    services = _services(request)
    services.store.delete_admin_session(request.cookies[COOKIE_NAME])
    response = Response(status_code=204)
    response.delete_cookie(COOKIE_NAME)
    return response


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------
@templates_router.get("/templates", response_model=m.Page[m.Template])
def list_templates(request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    templates = _services(request).catalog.list_templates()
    start = (page - 1) * limit
    return m.Page[m.Template](
        page=page, limit=limit, total=len(templates),
        items=templates[start:start + limit],
    )


@templates_router.get("/templates/{template_id}/versions/{version}", response_model=m.Template)
def get_template(request: Request, template_id: str, version: int):
    return _services(request).catalog.get_template(template_id, version)


# ---------------------------------------------------------------------------
# Documents
# ---------------------------------------------------------------------------
@documents_router.post("/documents", status_code=202, response_model=m.IngestAccepted)
def upload_document(
    request: Request,
    file: UploadFile,
    metadata: Annotated[str, Form()],
):
    _require_admin(request)
    services = _services(request)
    record = m.Metadata.model_validate_json(metadata)
    _check_origin(request)
    from ..knowledge.storage import filename_key
    filename_key(file.filename or '')
    with tempfile.TemporaryDirectory() as temporary:
        path=Path(temporary)/(file.filename or '')
        size=0
        with path.open('wb') as staging:
            while chunk:=file.file.read(1024*1024):
                size+=len(chunk)
                if size>50*1024*1024:
                    raise ServiceError(m.ErrorInfo(code='FILE_TOO_LARGE',message='文件超过 50 MiB。',retryable=False,details={}))
                staging.write(chunk)
        return services.knowledge.ingest_document(path,record)


@documents_router.get("/documents", response_model=m.Page[m.DocumentRecord])
def list_documents(request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    total, items = _services(request).store.list_documents(page, limit)
    return m.Page[m.DocumentRecord](page=page, limit=limit, total=total, items=items)


@documents_router.get("/documents/{document_id}", response_model=m.DocumentRecord)
def get_document(request: Request, document_id: str):
    record = _services(request).store.get_document(document_id)
    if record is None:
        raise ServiceError(m.ErrorInfo(
            code="RESOURCE_NOT_FOUND", message="未找到对应的资料。",
            retryable=False, details={"document_id": document_id},
        ))
    return record


@documents_router.get(
    "/documents/{document_id}/versions/{document_version_id}",
    response_model=m.DocumentRecord,
)
def get_document_version(request: Request, document_id: str, document_version_id: str):
    record = _services(request).store.get_document_version(document_id, document_version_id)
    if record is None:
        raise ServiceError(m.ErrorInfo(
            code="RESOURCE_NOT_FOUND", message="未找到对应的资料版本。",
            retryable=False,
            details={"document_id": document_id, "document_version_id": document_version_id},
        ))
    return record


# ---------------------------------------------------------------------------
# Reports and jobs
# ---------------------------------------------------------------------------
@reports_router.post("/reports", status_code=202, response_model=m.JobAccepted)
def create_report(
    request: Request,
    payload: Annotated[dict, Body()],
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
):
    return _services(request).orchestration.schedule_report(payload, idempotency_key)


@reports_router.get("/reports", response_model=m.Page[m.ReportSummary])
def list_reports(request: Request, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    services = _services(request)
    total, reports = services.store.list_reports(page, limit)
    return m.Page[m.ReportSummary](
        page=page, limit=limit, total=total,
        items=[services.orchestration.report_summary(report) for report in reports],
    )


@reports_router.get("/reports/{report_id}", response_model=m.ReportSummary)
def get_report(request: Request, report_id: str):
    report = _services(request).store.get_report(report_id)
    if report is None:
        raise ServiceError(m.ErrorInfo(
            code="RESOURCE_NOT_FOUND", message="未找到对应的报告。",
            retryable=False, details={"report_id": report_id},
        ))
    return _services(request).orchestration.report_summary(report)


@reports_router.get("/reports/{report_id}/versions", response_model=m.Page[m.Version])
def list_versions(request: Request, report_id: str, page: int = Query(1, ge=1), limit: int = Query(20, ge=1, le=100)):
    if _services(request).store.get_report(report_id) is None:
        raise ServiceError(m.ErrorInfo(
            code="RESOURCE_NOT_FOUND", message="未找到对应的报告。",
            retryable=False, details={"report_id": report_id},
        ))
    total, versions = _services(request).store.list_versions(report_id, page, limit)
    return m.Page[m.Version](page=page, limit=limit, total=total, items=versions)


@reports_router.get(
    "/reports/{report_id}/versions/{version_id}", response_model=m.ReportDocument,
)
def get_version(request: Request, report_id: str, version_id: str):
    document = _services(request).store.get_version(report_id, version_id)
    if document is None:
        raise ServiceError(m.ErrorInfo(
            code="RESOURCE_NOT_FOUND", message="未找到对应的报告版本。",
            retryable=False,
            details={"report_id": report_id, "version_id": version_id},
        ))
    return document


@reports_router.get("/reports/{report_id}/versions/{version_id}/assets/{asset_id}")
def get_asset(request: Request, report_id: str, version_id: str, asset_id: str):
    services = _services(request)
    if services.store.get_version(report_id, version_id) is None:
        raise ServiceError(m.ErrorInfo(
            code="RESOURCE_NOT_FOUND", message="未找到对应的报告版本。",
            retryable=False, details={"report_id": report_id, "version_id": version_id},
        ))
    path = (services.orchestration.runtime_root / "assets" / version_id / f"{asset_id}.png").resolve()
    if not path.is_relative_to(services.orchestration.runtime_root.resolve()) or not path.is_file():
        raise ServiceError(m.ErrorInfo(
            code="RESOURCE_NOT_FOUND", message="未找到对应的图表资源。",
            retryable=False, details={"asset_id": asset_id},
        ))
    return FileResponse(path, media_type="image/png", filename=f"{asset_id}.png")


@jobs_router.get("/jobs/{job_id}", response_model=m.JobDetail)
def get_job(request: Request, job_id: str):
    job = _services(request).store.get_job(job_id)
    if job is None:
        raise ServiceError(m.ErrorInfo(
            code="RESOURCE_NOT_FOUND", message="未找到对应的任务。",
            retryable=False, details={"job_id": job_id},
        ))
    return _services(request).orchestration.job_detail(job)


@jobs_router.get("/jobs/{job_id}/chapters/{chapter_id}", response_model=m.Preview)
def get_chapter_preview(request: Request, job_id: str, chapter_id: str):
    return _services(request).orchestration.preview(job_id, chapter_id)


@jobs_router.post("/jobs/{job_id}/cancel", response_model=m.JobDetail)
def cancel_job(request: Request, job_id: str):
    return _services(request).orchestration.cancel_job(job_id)


@jobs_router.post("/jobs/{job_id}/retry", status_code=202, response_model=m.JobAccepted)
def retry_job(
    request: Request,
    job_id: str,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
):
    return _services(request).orchestration.retry_job(job_id, idempotency_key)


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------
@exports_router.post(
    "/reports/{report_id}/versions/{version_id}/exports",
    status_code=202, response_model=m.ExportAccepted,
)
def create_export(
    request: Request,
    report_id: str,
    version_id: str,
    body: m.ExportRequest,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=128)],
):
    return _services(request).orchestration.create_export(
        report_id, version_id, body.format, idempotency_key,
    )


def _export_summary(services: AppServices, export) -> m.ExportSummary:
    download_url = (
        f"/api/v1/exports/{export.export_id}/download"
        if export.status == "READY" else None
    )
    return m.ExportSummary(
        export_id=export.export_id, version_id=export.version_id,
        format=export.format, status=export.status, checksum=export.checksum,
        download_url=download_url, error=export.error,
    )


@exports_router.get("/exports/{export_id}", response_model=m.ExportSummary)
def get_export(request: Request, export_id: str):
    services = _services(request)
    export = services.store.get_export(export_id)
    if export is None:
        raise ServiceError(m.ErrorInfo(
            code="RESOURCE_NOT_FOUND", message="未找到对应的导出任务。",
            retryable=False, details={"export_id": export_id},
        ))
    return _export_summary(services, export)


@exports_router.get("/exports/{export_id}/download")
def download_export(request: Request, export_id: str):
    path, media, filename = _services(request).orchestration.export_file(export_id)
    return FileResponse(path, media_type=media, filename=filename)
