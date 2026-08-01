"""Transport-level guards applied to every request.

The desktop app had no attack surface of this kind: there was no request, no origin, and the
only body it read was a camera frame it had captured itself.
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.errors import ErrorCode

# Applies to JSON request bodies only. Frames arrive over the WebSocket and are bounded
# separately by AGROVISION_INGEST__MAX_FRAME_BYTES, which is a much larger and quite different limit.
DEFAULT_MAX_BODY_BYTES = 256 * 1024


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects oversized request bodies with 413.

    Starlette imposes no limit of its own, so without this a single POST could stream an
    arbitrary amount into memory before any handler ran.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = DEFAULT_MAX_BODY_BYTES) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next):
        declared = request.headers.get("content-length")

        if declared is not None:
            try:
                if int(declared) > self._max_bytes:
                    return self._too_large()
            except ValueError:
                return self._too_large()

        return await call_next(request)

    def _too_large(self) -> JSONResponse:
        return JSONResponse(
            status_code=413,
            content={
                "error": {
                    "code": ErrorCode.VALIDATION_ERROR,
                    "message": f"Request body exceeds {self._max_bytes} bytes",
                }
            },
        )


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds defensive response headers.

    This is a JSON API rather than a rendered site, so the headers that matter are the ones
    stopping a browser from reinterpreting a response: sniffing a JSON body as HTML, or framing
    it. HSTS is only sent when the deployment is actually on TLS — asserting it from a plain
    HTTP dev server would pin developers' browsers to https://localhost.
    """

    def __init__(self, app: ASGIApp, hsts: bool = False) -> None:
        super().__init__(app)
        self._hsts = hsts

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)

        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")

        # API responses execute nothing. The same FastAPI process also serves the bundled web
        # client at / and /static, so those two paths receive a narrow same-origin policy that
        # permits its stylesheet, script, camera preview and scanning WebSocket.
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            content_security_policy = (
                "default-src 'self'; "
                "script-src 'self'; "
                "style-src 'self'; "
                "img-src 'self' data: blob:; "
                "media-src 'self' blob:; "
                "connect-src 'self' ws: wss:; "
                "object-src 'none'; "
                "base-uri 'self'; "
                "form-action 'self'; "
                "frame-ancestors 'none'"
            )
        else:
            content_security_policy = "default-src 'none'; frame-ancestors 'none'"

        response.headers.setdefault("Content-Security-Policy", content_security_policy)

        if self._hsts:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )

        return response


def client_ip(request: Request) -> str:
    """Best-effort client address for rate limiting.

    ``X-Forwarded-For`` is honoured only when the deployment says it is behind a trusted proxy.
    Trusting it unconditionally would make every limit here bypassable by setting a header.
    """
    if getattr(request.app.state, "trust_proxy_headers", False):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()

    return request.client.host if request.client else "unknown"
