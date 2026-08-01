"""Stable error codes and the handlers that render them.

Every failure carries a machine-readable ``code``. The frontend switches on that string, so
these values are part of the API contract and must not be reworded casually — unlike ``message``,
which is for humans and free to change.
"""

from __future__ import annotations

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorCode:
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    TOKEN_INVALID = "TOKEN_INVALID"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    EMAIL_TAKEN = "EMAIL_TAKEN"
    ACCOUNT_DISABLED = "ACCOUNT_DISABLED"
    NOT_FOUND = "NOT_FOUND"
    SESSION_ALREADY_OPEN = "SESSION_ALREADY_OPEN"
    SESSION_CLOSED = "SESSION_CLOSED"
    RATE_LIMITED = "RATE_LIMITED"
    TOO_MANY_CONNECTIONS = "TOO_MANY_CONNECTIONS"
    RAG_UNAVAILABLE = "RAG_UNAVAILABLE"
    RAG_QUERY_FAILED = "RAG_QUERY_FAILED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ErrorBody(BaseModel):
    code: str
    message: str
    detail: dict | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


class ApiError(Exception):
    """Raised anywhere in the stack; rendered by :func:`install_error_handlers`."""

    def __init__(
        self,
        code: str,
        message: str,
        status_code: int = status.HTTP_400_BAD_REQUEST,
        detail: dict | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.detail = detail
        self.headers = headers


class NotFound(ApiError):
    """Used for both "does not exist" and "not yours".

    Deliberately indistinguishable: a 403 on someone else's session id would confirm that the
    id is real, letting an attacker enumerate which sessions exist.
    """

    def __init__(self, message: str = "Not found") -> None:
        super().__init__(ErrorCode.NOT_FOUND, message, status.HTTP_404_NOT_FOUND)


class Unauthenticated(ApiError):
    def __init__(
        self, code: str = ErrorCode.UNAUTHENTICATED, message: str = "Authentication required"
    ) -> None:
        super().__init__(
            code,
            message,
            status.HTTP_401_UNAUTHORIZED,
            headers={"WWW-Authenticate": "Bearer"},
        )


def _render(status_code: int, code: str, message: str, detail=None, headers=None):
    body = {"error": {"code": code, "message": message}}
    if detail is not None:
        body["error"]["detail"] = detail
    return JSONResponse(status_code=status_code, content=body, headers=headers)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_request: Request, exc: ApiError):
        return _render(exc.status_code, exc.code, exc.message, exc.detail, exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_request: Request, exc: RequestValidationError):
        return _render(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            ErrorCode.VALIDATION_ERROR,
            "Request payload is invalid",
            {"fields": _summarise(exc)},
        )


def _summarise(exc: RequestValidationError) -> list[dict[str, str]]:
    return [
        {
            "field": ".".join(str(part) for part in error.get("loc", ())[1:]),
            "message": error.get("msg", ""),
        }
        for error in exc.errors()
    ]
