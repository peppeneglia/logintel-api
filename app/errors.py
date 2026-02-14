"""
Consistent error handling — FRD Section 8.3.

All API errors return:
    {"error": {"code": "...", "message": "...", "details": [...]}}
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.models.schemas import ErrorBody, ErrorDetail, ErrorResponse


# --- Custom exceptions ---

class LogintelError(Exception):
    """Base exception for Logintel API."""

    def __init__(self, code: str, message: str, status_code: int = 500, details: list[ErrorDetail] | None = None):
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []
        super().__init__(message)


class InvalidRequestError(LogintelError):
    def __init__(self, message: str = "Invalid request parameters", details: list[ErrorDetail] | None = None):
        super().__init__("INVALID_REQUEST", message, 400, details)


class UnauthorizedError(LogintelError):
    def __init__(self, message: str = "Authentication required"):
        super().__init__("UNAUTHORIZED", message, 401)


class NotFoundError(LogintelError):
    def __init__(self, message: str = "Resource not found"):
        super().__init__("NOT_FOUND", message, 404)


class RateLimitError(LogintelError):
    def __init__(self, message: str = "Rate limit exceeded", retry_after: int = 60):
        self.retry_after = retry_after
        super().__init__("RATE_LIMIT_EXCEEDED", message, 429)


class ServiceUnavailableError(LogintelError):
    def __init__(self, message: str = "External service unavailable"):
        super().__init__("SERVICE_UNAVAILABLE", message, 502)


# --- Exception handlers ---

def register_error_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the FastAPI app."""

    @app.exception_handler(LogintelError)
    async def logintel_error_handler(_request: Request, exc: LogintelError) -> JSONResponse:
        body = ErrorResponse(
            error=ErrorBody(code=exc.code, message=exc.message, details=exc.details)
        )
        headers: dict[str, str] = {}
        if exc.status_code == 429 and isinstance(exc, RateLimitError):
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(status_code=exc.status_code, content=body.model_dump(), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
        details = []
        for err in exc.errors():
            field = " -> ".join(str(loc) for loc in err["loc"]) if err.get("loc") else None
            details.append(ErrorDetail(field=field, message=err["msg"]))
        body = ErrorResponse(
            error=ErrorBody(
                code="INVALID_REQUEST",
                message="Validation error",
                details=details,
            )
        )
        return JSONResponse(status_code=400, content=body.model_dump())

    @app.exception_handler(Exception)
    async def generic_error_handler(_request: Request, _exc: Exception) -> JSONResponse:
        body = ErrorResponse(
            error=ErrorBody(code="INTERNAL_ERROR", message="An unexpected error occurred")
        )
        return JSONResponse(status_code=500, content=body.model_dump())
