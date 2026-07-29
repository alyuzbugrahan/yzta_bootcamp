"""Rate-limit dependencies.

Two different keys, because the two failure modes are different:

* **Credential endpoints** are keyed by IP. There is no user yet — that is the thing being
  guessed — so the source address is the only handle available.
* **Authenticated endpoints** are keyed by user id. Keying those by IP would punish an entire
  co-operative behind one connection while doing nothing about a single account being driven
  hard from many addresses.
"""

from __future__ import annotations

from fastapi import Request, status

from app.core.errors import ApiError, ErrorCode
from app.core.logging import get_logger
from app.core.middleware import client_ip

log = get_logger(__name__)


class RateLimited(ApiError):
    def __init__(self, retry_after: float) -> None:
        super().__init__(
            ErrorCode.RATE_LIMITED,
            "Too many requests",
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail={"retry_after": retry_after},
            headers={"Retry-After": str(max(1, int(retry_after + 0.999)))},
        )


async def throttle_auth(request: Request) -> None:
    """Per-IP limit for register, login and refresh."""
    limiter = getattr(request.app.state, "auth_limiter", None)
    if limiter is None:
        return

    key = client_ip(request)
    allowed, retry_after = limiter.check(key)

    if not allowed:
        log.warning("auth_rate_limited", ip=key, path=request.url.path)
        raise RateLimited(retry_after)
