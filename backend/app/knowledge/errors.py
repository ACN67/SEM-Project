"""Stable service errors shared by the indexing pipeline and retrieval."""
from ..contracts import models as m
from ..contracts.services import ServiceError

def infrastructure_error(exc, code="DOCUMENT_STORAGE_ERROR"):
    if isinstance(exc, ServiceError):
        return exc
    return ServiceError(m.ErrorInfo(code=code, message="Knowledge infrastructure unavailable",
        retryable=True, details={}))
