"""Filesystem storage, for development and single-node deployments.

Closest to what the desktop app did, but it does not survive horizontal scaling: a second
replica cannot read what the first wrote. Use :mod:`app.infra.storage.s3` in production.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import anyio.to_thread

from app.core.logging import get_logger
from app.infra.storage.base import ObjectNotFound, StorageError, validate_key

log = get_logger(__name__)


class LocalStorage:
    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    @property
    def root(self) -> Path:
        return self._root

    def _path(self, key: str) -> Path:
        validate_key(key)
        path = (self._root / key).resolve()

        # Belt and braces: validate_key already rejects traversal, but a symlink inside the
        # root could still resolve outside it.
        if not path.is_relative_to(self._root):
            raise StorageError(f"Object key escapes the storage root: {key!r}")

        return path

    async def put(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        path = self._path(key)
        await anyio.to_thread.run_sync(self._write, path, data)

    @staticmethod
    def _write(path: Path, data: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write-then-rename, so a crash mid-write cannot leave a truncated JPEG that later
        # reads as a corrupt image.
        temporary = path.with_suffix(path.suffix + ".part")
        temporary.write_bytes(data)
        temporary.replace(path)

    async def get(self, key: str) -> bytes:
        path = self._path(key)
        try:
            return await anyio.to_thread.run_sync(path.read_bytes)
        except FileNotFoundError as exc:
            raise ObjectNotFound(key) from exc

    async def exists(self, key: str) -> bool:
        return await anyio.to_thread.run_sync(self._path(key).is_file)

    async def delete_prefix(self, prefix: str) -> int:
        directory = self._path(prefix.rstrip("/"))
        return await anyio.to_thread.run_sync(self._remove_tree, directory)

    @staticmethod
    def _remove_tree(directory: Path) -> int:
        if not directory.is_dir():
            return 0
        count = sum(1 for path in directory.rglob("*") if path.is_file())
        shutil.rmtree(directory, ignore_errors=True)
        return count

    def presigned_url(self, key: str, expires_in: int = 300) -> str | None:
        """None — a local file has no directly-fetchable URL.

        The API endpoint streams the bytes instead, which also keeps the ownership check on the
        request path rather than trusting a signature.
        """
        return None
