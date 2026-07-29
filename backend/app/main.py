"""FastAPI application factory.

Replaces ``main.py``, which built a Qt window and a splash screen. Process-wide resources — the
model, the database engine — are created once during lifespan startup and shared; per-connection
state is built per WebSocket in :mod:`app.services.scan_service` (Phase 4).
"""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1 import auth, exports, health, images, reports, sessions, ws_scan
from app.config import Settings, get_settings
from app.core.errors import install_error_handlers
from app.core.logging import configure_logging, get_logger
from app.core.middleware import BodySizeLimitMiddleware, SecurityHeadersMiddleware
from app.core.rate_limit import ConcurrencyLimiter, RateLimit, TokenBucketLimiter
from app.infra.archiver import ImageArchiver
from app.infra.db.session import create_engine, create_session_factory
from app.infra.detector_provider import build_detector
from app.infra.model_pool import InferencePool
from app.infra.storage.provider import build_storage
from app.services.scan_service import ConnectionRegistry

log = get_logger(__name__)

API_PREFIX = "/api/v1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings: Settings = app.state.settings

    engine = create_engine(settings.database.url, echo=settings.database.echo)
    app.state.engine = engine
    app.state.session_factory = create_session_factory(engine)

    # Loading here rather than per request means one model per process. Deploy with a single
    # uvicorn worker and scale with replicas; multiple workers would multiply model memory.
    app.state.detector = build_detector(settings)

    # Bounds how many frames are in inference at once across every connected farmer.
    app.state.inference_pool = InferencePool(settings.max_concurrent_inferences)
    app.state.connection_registry = ConnectionRegistry()

    app.state.storage = build_storage(settings)
    if app.state.storage is not None:
        app.state.archiver = ImageArchiver(
            app.state.storage,
            max_queue=settings.storage.queue_size,
            workers=settings.storage.workers,
        )
        app.state.archiver.start()

    log.info("startup_complete", environment=settings.environment)
    try:
        yield
    finally:
        if app.state.archiver is not None:
            await app.state.archiver.stop()
        await engine.dispose()
        log.info("shutdown")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(level=settings.log_level, json_output=settings.log_json)

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.detector = None
    app.state.engine = None
    app.state.session_factory = None
    app.state.inference_pool = None
    app.state.connection_registry = None
    app.state.storage = None
    app.state.archiver = None
    app.state.trust_proxy_headers = settings.security.trust_proxy_headers

    app.state.auth_limiter = TokenBucketLimiter(
        RateLimit(settings.security.auth_attempts, settings.security.auth_window_seconds)
    )
    app.state.connection_limiter = ConcurrencyLimiter(
        settings.security.max_connections_per_user
    )

    install_error_handlers(app)

    # Outermost first: headers are attached to every response including rejections, and an
    # oversized body is refused before any handler or limiter runs.
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.security.hsts)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.security.max_body_bytes)

    if settings.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=settings.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    for router in (
        health.router,
        auth.router,
        sessions.router,
        exports.router,
        reports.router,
        images.router,
        ws_scan.router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    return app


app = create_app()
