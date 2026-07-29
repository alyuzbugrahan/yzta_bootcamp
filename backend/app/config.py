"""Application settings.

Replaces ``utils/config_manager.py``. The desktop ConfigManager was a ``__new__``
singleton that silently ignored its ``config_path`` argument after first construction,
which made it impossible to instantiate with different values in a test. Settings are
now plain immutable models built from the environment.

Environment variables use the ``FIGION_`` prefix and ``__`` for nesting::

    FIGION_MODEL__CONF_THRESHOLD=0.6
    FIGION_VISION__MIN_AREA_RATIO=0.01
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Placeholder signing key. Refused outside the dev environment; see Settings._reject_dev_secret.
# At least 32 bytes, the HMAC-SHA256 minimum in RFC 7518 §3.2 — a shorter key weakens the
# signature regardless of environment.
DEV_SECRET_KEY = "dev-insecure-secret-change-me-000000"

MIN_SECRET_KEY_LENGTH = 32


class ModelSettings(BaseModel):
    """Inference model configuration (``[model]`` in the old config.ini)."""

    path: str = "models/final_model.pt"
    conf_threshold: float = Field(default=0.50, ge=0.0, le=1.0)
    iou_threshold: float = Field(default=0.45, ge=0.0, le=1.0)
    input_size: int = Field(default=640, gt=0)

    # The desktop app fell back to demo mode whenever the model file was missing
    # (inference_engine.py:102), which is a silent failure in production. Demo mode
    # now has to be asked for.
    allow_demo: bool = False


class VisionSettings(BaseModel):
    """Fig-candidate detection parameters (``[vision]`` in the old config.ini).

    ``min_candidate_area_px`` from the desktop config is deliberately absent. It was an
    absolute pixel count tuned for the rig's fixed 1280x720 capture; browser clients send
    arbitrary resolutions, at which the same physical fig falls under the threshold and is
    silently dropped. The two ratio thresholds below are resolution-independent and cover
    the same intent.
    """

    min_area_ratio: float = Field(default=0.006, gt=0.0, le=1.0)
    max_area_ratio: float = Field(default=0.80, gt=0.0, le=1.0)
    padding_ratio: float = Field(default=0.08, ge=0.0)
    min_aspect: float = Field(default=0.35, gt=0.0)
    max_aspect: float = Field(default=2.85, gt=0.0)
    min_fill_ratio: float = Field(default=0.25, ge=0.0, le=1.0)


class TimingSettings(BaseModel):
    """Confirmation and cooldown gates.

    The desktop pipeline counted frames, tuned against a local 30 fps camera. Over a network
    the effective rate is 5-10 fps, so a bare frame count stretches wall-clock behaviour by
    3-6x, while a bare duration collapses to a single sample and defeats the anti-flicker
    filter entirely. Each gate therefore carries *both* floors and fires only when both are
    met. See ``app/domain/gating.py``.

    Sample floors reproduce the desktop constants; the second floors reproduce what those
    constants meant at 30 fps.
    """

    # inference_engine.py: stability_required / max_missing_frames
    confirm_samples: int = Field(default=2, ge=1)
    confirm_seconds: float = Field(default=0.07, ge=0.0)
    lost_samples: int = Field(default=3, ge=1)
    lost_seconds: float = Field(default=0.10, ge=0.0)

    # video_processor_worker.py: PRESENCE_CONFIRM_FRAMES / COOLDOWN_FRAMES
    presence_samples: int = Field(default=3, ge=1)
    presence_seconds: float = Field(default=0.10, ge=0.0)
    cooldown_samples: int = Field(default=8, ge=1)
    cooldown_seconds: float = Field(default=0.27, ge=0.0)

    # inference_engine.py: stable_iou_threshold / video_processor_worker.py: IOU_MATCH_THRESHOLD
    track_iou_threshold: float = Field(default=0.35, ge=0.0, le=1.0)
    slot_iou_threshold: float = Field(default=0.25, ge=0.0, le=1.0)


class IngestSettings(BaseModel):
    """Limits applied to client-supplied frames before they reach OpenCV.

    ``cv2.imdecode`` on unvalidated attacker-controlled bytes is a memory-exhaustion
    vector; these bounds are checked first.
    """

    max_frame_bytes: int = Field(default=2 * 1024 * 1024, gt=0)
    max_frame_width: int = Field(default=1920, gt=0)
    max_frame_height: int = Field(default=1080, gt=0)
    max_fps: float = Field(default=15.0, gt=0.0)


class DatabaseSettings(BaseModel):
    """PostgreSQL is the deployment target.

    The default points at a local SQLite file so the suite and a fresh checkout run without
    infrastructure. Nothing in the queries is dialect-specific — the desktop DAO's
    ``SUM(decision = 'Aflatoxin')`` was, and is replaced by ``COUNT(*) FILTER``.
    """

    url: str = "sqlite+aiosqlite:///./figion.db"
    echo: bool = False


class StorageSettings(BaseModel):
    """Where inspection images live.

    ``local`` mirrors the desktop behaviour and is fine for one node; it does not survive
    horizontal scaling, since a second replica cannot read what the first wrote.
    """

    backend: Literal["local", "s3"] = "local"

    # local
    root: str = "data/images"

    # s3 / MinIO
    bucket: str = "figion-images"
    endpoint_url: str = ""
    region: str = "us-east-1"
    access_key: str = ""
    secret_key: str = ""

    # Short, because anyone holding a presigned URL can fetch the object until it expires.
    presign_ttl_seconds: int = Field(default=300, gt=0, le=3600)

    # Archiving is best-effort: a slow backend must cost images, never scanning throughput.
    queue_size: int = Field(default=200, gt=0)
    workers: int = Field(default=4, gt=0)

    # Turning this off keeps decisions and statistics while storing no images at all — the
    # cheapest answer to the retention question if evidence is not needed.
    enabled: bool = True


class AuthSettings(BaseModel):
    """Token issuance and password hashing.

    ``secret_key`` has a development default so a fresh checkout runs. ``Settings`` refuses to
    start with that value outside dev — a signing key that ships in the repository lets anyone
    mint a token for any farmer.
    """

    secret_key: str = Field(default=DEV_SECRET_KEY, min_length=MIN_SECRET_KEY_LENGTH)
    algorithm: str = "HS256"
    access_ttl_minutes: int = Field(default=15, gt=0)
    refresh_ttl_days: int = Field(default=30, gt=0)
    min_password_length: int = Field(default=8, ge=8)


class SecuritySettings(BaseModel):
    """Rate limits and transport guards.

    Limits are per-process. With more than one replica the effective ceiling multiplies; see
    ``app/core/rate_limit.py``.
    """

    # Credential endpoints, keyed by client IP. Tight enough to make online password guessing
    # useless, loose enough that a household behind one NAT address is not locked out.
    auth_attempts: int = Field(default=10, ge=1)
    auth_window_seconds: float = Field(default=60.0, gt=0)

    # Everything else, keyed by authenticated user.
    api_requests: int = Field(default=240, ge=1)
    api_window_seconds: float = Field(default=60.0, gt=0)

    # Simultaneous live scan sockets per farmer. More than a couple means either several
    # devices or a leak; either way it should not be unbounded.
    max_connections_per_user: int = Field(default=3, ge=1)

    max_body_bytes: int = Field(default=256 * 1024, gt=0)

    # Only enable behind a proxy that overwrites X-Forwarded-For. Otherwise any client can
    # forge the address every rate limit is keyed by.
    trust_proxy_headers: bool = False

    # Emitted only when the deployment terminates TLS.
    hsts: bool = False


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="FIGION_",
        env_nested_delimiter="__",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "Figion"
    app_version: str = "0.1.0"
    environment: Literal["dev", "staging", "prod"] = "dev"
    log_level: str = "INFO"
    log_json: bool = True

    auth: AuthSettings = AuthSettings()
    security: SecuritySettings = SecuritySettings()
    storage: StorageSettings = StorageSettings()
    database: DatabaseSettings = DatabaseSettings()
    model: ModelSettings = ModelSettings()
    vision: VisionSettings = VisionSettings()
    timing: TimingSettings = TimingSettings()
    ingest: IngestSettings = IngestSettings()

    cors_origins: list[str] = []

    # Bounded pool for blocking inference calls. Sized in Phase 6 against real load;
    # 0 means "derive from cpu_count".
    max_concurrent_inferences: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _reject_dev_secret(self) -> Settings:
        if self.environment != "dev" and self.auth.secret_key == DEV_SECRET_KEY:
            raise ValueError(
                "FIGION_AUTH__SECRET_KEY must be set outside the dev environment; "
                "the default key is public and would let anyone forge a token."
            )
        return self

    @model_validator(mode="after")
    def _reject_wildcard_cors(self) -> Settings:
        """A wildcard origin with credentials enabled would let any site call the API as the
        logged-in farmer. Browsers reject that combination, but the misconfiguration is worth
        catching at boot rather than as a confusing CORS failure in the frontend."""
        if self.environment != "dev" and "*" in self.cors_origins:
            raise ValueError(
                "FIGION_CORS_ORIGINS must name explicit origins outside dev, not '*'."
            )
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
