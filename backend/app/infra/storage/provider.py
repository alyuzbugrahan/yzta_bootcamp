"""Storage backend construction."""

from __future__ import annotations

from app.config import Settings
from app.core.logging import get_logger
from app.infra.storage.base import StorageBackend
from app.infra.storage.local import LocalStorage

log = get_logger(__name__)


def build_storage(settings: Settings) -> StorageBackend | None:
    """Return the configured backend, or None when archiving is disabled."""
    if not settings.storage.enabled:
        log.warning("image_archiving_disabled")
        return None

    if settings.storage.backend == "s3":
        from app.infra.storage.s3 import S3Storage

        storage = S3Storage.from_settings(settings.storage)
        storage.ensure_bucket()
        log.info("storage_ready", backend="s3", bucket=settings.storage.bucket)
        return storage

    storage = LocalStorage(settings.storage.root)
    log.info("storage_ready", backend="local", root=str(storage.root))
    return storage
