"""HTTP error mapping: ServiceError codes to statuses, unified Error body."""
from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError as PydanticValidationError

from ..contracts import models as m
from ..contracts.services import ServiceError

ERROR_STATUS: dict[str, int] = {
    "VALIDATION_ERROR": 422,
    "INVALID_FILENAME": 422,
    "INVALID_REQUEST": 422,
    "TEMPLATE_NOT_FOUND": 404,
    "RESOURCE_NOT_FOUND": 404,
    "DOCUMENT_NOT_FOUND": 404,
    "IDEMPOTENCY_CONFLICT": 409,
    "REPORT_VERSION_CONFLICT": 409,
    "REPORT_NOT_EXPORTABLE": 409,
    "EXPORT_NOT_READY": 409,
    "EXPORT_FAILED": 409,
    "INVALID_JOB_STATE": 409,
    "FILE_TOO_LARGE": 413,
    "UNSUPPORTED_FILE_TYPE": 415,
    "BUDGET_LIMIT": 429,
    "MODEL_RATE_LIMIT": 429,
    "MODEL_TIMEOUT": 503,
    "DOCUMENT_STORE_UNAVAILABLE": 503,
    "AUTH_REQUIRED": 401,
}


def status_for(error: m.ErrorInfo) -> int:
    return ERROR_STATUS.get(error.code, 500)


def error_response(error: m.ErrorInfo, status: int | None = None) -> JSONResponse:
    body = m.Error(error=error).model_dump(by_alias=True)
    return JSONResponse(status_code=status or status_for(error), content=body)


def _pointer(parts: tuple) -> str:
    return "".join(
        "/" + str(part).replace("~", "~0").replace("/", "~1") for part in parts
    )


def _validation_body(errors: list[tuple[tuple, str]]) -> dict:
    return m.Error(error=m.ErrorInfo(
        code="VALIDATION_ERROR", message="请求参数校验失败。", retryable=False,
        details={"errors": [{"path": _pointer(path), "message": message}
                            for path, message in errors]},
    )).model_dump(by_alias=True)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ServiceError)
    async def handle_service_error(_: Request, exc: ServiceError) -> JSONResponse:
        return error_response(exc.error)

    @app.exception_handler(RequestValidationError)
    async def handle_request_validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            (error["loc"], "请求格式不符合接口要求。") for error in exc.errors()
        ]
        return JSONResponse(status_code=422, content=_validation_body(errors))

    @app.exception_handler(PydanticValidationError)
    async def handle_pydantic_validation(_: Request, exc: PydanticValidationError) -> JSONResponse:
        errors = [(error["loc"], "字段不符合要求。") for error in exc.errors()]
        return JSONResponse(status_code=422, content=_validation_body(errors))
