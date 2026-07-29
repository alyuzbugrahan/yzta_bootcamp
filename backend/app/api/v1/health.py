"""Liveness, readiness and model introspection.

Replaces ``control/hardware_monitor.py``, which pinged a local camera. There is no server-side
camera any more; camera availability is a client concern, reported by the browser's permission
prompt. What matters here is whether this process can serve traffic.

Liveness and readiness are deliberately separate. A liveness probe answers "is this process
wedged, should it be killed"; a readiness probe answers "should traffic be routed here right
now". Conflating them means a brief database blip restarts every replica instead of draining
them, turning a recoverable dependency failure into an outage.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text

from app.core.logging import get_logger
from app.domain.models import CLASS_NAMES

log = get_logger(__name__)

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    version: str
    environment: str


class ReadinessResponse(BaseModel):
    ready: bool
    checks: dict[str, str]


class ModelInfoResponse(BaseModel):
    backend: str
    demo_mode: bool
    class_names: list[str]
    input_size: int


@router.get("/health", response_model=HealthResponse)
async def health(request: Request) -> HealthResponse:
    """Liveness. Answers from process state alone — never touches a dependency.

    A probe that queried the database would restart healthy replicas during a database
    failover, which is the opposite of what should happen.
    """
    settings = request.app.state.settings
    return HealthResponse(
        status="ok",
        version=settings.app_version,
        environment=settings.environment,
    )


@router.get("/health/ready", response_model=ReadinessResponse)
async def readiness(request: Request, response: Response) -> ReadinessResponse:
    """Readiness. Verifies the dependencies a request actually needs.

    Returns 503 when any check fails, so the orchestrator stops routing here while leaving the
    process alive to recover.
    """
    checks: dict[str, str] = {}

    checks["database"] = await _check_database(request)
    checks["model"] = _check_model(request)
    checks["storage"] = await _check_storage(request)

    ready = all(value == "ok" for value in checks.values())

    if not ready:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        log.warning("readiness_failed", checks=checks)

    return ReadinessResponse(ready=ready, checks=checks)


async def _check_database(request: Request) -> str:
    factory = request.app.state.session_factory

    if factory is None:
        return "not configured"

    try:
        async with factory() as session:
            await session.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - any failure means not ready
        return f"error: {type(exc).__name__}"

    return "ok"


def _check_model(request: Request) -> str:
    detector = request.app.state.detector

    if detector is None:
        # build_detector returns None only when demo mode was explicitly permitted; the
        # process refuses to start otherwise. Serving simulated results is a deliberate
        # configuration, so it is reported rather than treated as a failure.
        return "demo"

    return "ok"


async def _check_storage(request: Request) -> str:
    storage = request.app.state.storage

    if storage is None:
        return "disabled"

    try:
        # A miss is a perfectly good answer: it proves the backend responded.
        await storage.exists("__readiness_probe__")
    except Exception as exc:  # noqa: BLE001 - any failure means not ready
        return f"error: {type(exc).__name__}"

    return "ok"


@router.get("/model/info", response_model=ModelInfoResponse)
async def model_info(request: Request) -> ModelInfoResponse:
    settings = request.app.state.settings
    detector = request.app.state.detector

    return ModelInfoResponse(
        backend=detector.backend if detector is not None else "demo",
        demo_mode=detector is None,
        class_names=list(CLASS_NAMES),
        input_size=settings.model.input_size,
    )
