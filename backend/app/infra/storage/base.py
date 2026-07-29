"""Object storage abstraction and key construction.

Replaces ``utils/path_builder.py`` and the filesystem half of ``data/image_archiver.py``. The
desktop app wrote ``data/images/<date>/<batch_id>/Fig_0001_Healthy.jpg`` on the farmer's own
machine, where the path was meaningful and the disk was their problem. Neither holds now: a
browser cannot open a server path, and the storage bill is shared.

The date folder is replaced by user scoping. Grouping by date made sense when one operator's
runs were the only thing on disk; here the first partition has to be the tenant, because it is
what every listing, deletion and access check is scoped by.
"""

from __future__ import annotations

import re
from typing import Protocol, runtime_checkable

# Keys are built from server-controlled values, but a traversal guard is cheap and the cost of
# being wrong is writing outside the storage root.
_SAFE_SEGMENT = re.compile(r"^[A-Za-z0-9._-]+$")


class StorageError(RuntimeError):
    pass


class ObjectNotFound(StorageError):
    pass


@runtime_checkable
class StorageBackend(Protocol):
    """What the application needs of object storage."""

    async def put(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None: ...

    async def get(self, key: str) -> bytes: ...

    async def exists(self, key: str) -> bool: ...

    async def delete_prefix(self, prefix: str) -> int:
        """Delete every object under ``prefix``. Returns how many were removed."""
        ...

    def presigned_url(self, key: str, expires_in: int = 300) -> str | None:
        """A directly-fetchable URL, or None if the backend cannot issue one."""
        ...


def validate_key(key: str) -> str:
    """Reject keys that could escape the storage root.

    ``..`` segments, absolute paths and backslashes are refused rather than normalised —
    silently rewriting a suspicious key hides the fact that something upstream produced it.
    """
    if not key or key != key.strip():
        raise StorageError("Object key is empty or padded")

    if key.startswith("/") or "\\" in key:
        raise StorageError(f"Object key is not relative: {key!r}")

    for segment in key.split("/"):
        # Checked before the character class: "." is a legal filename character, so ".." matches
        # _SAFE_SEGMENT and would otherwise sail through as an ordinary segment.
        if segment in {"", ".", ".."}:
            raise StorageError(f"Traversal or empty segment in object key {key!r}")

        if not _SAFE_SEGMENT.match(segment):
            raise StorageError(f"Unsafe segment {segment!r} in object key {key!r}")

    return key


def image_key(user_id: int, batch_id: str, fig_seq: int, decision: str) -> str:
    """``u{user_id}/{batch_id}/fig_{seq:04d}_{decision}.jpg``.

    Keeps the desktop filename shape (``utils/path_builder.py:16``) so an exported archive is
    still recognisable, with the date folder swapped for the owning user.
    """
    return validate_key(f"u{user_id}/{batch_id}/fig_{fig_seq:04d}_{decision}.jpg")


def session_prefix(user_id: int, batch_id: str) -> str:
    """Everything belonging to one scanning session, for bulk deletion."""
    validate_key(f"u{user_id}/{batch_id}/placeholder")
    return f"u{user_id}/{batch_id}/"
