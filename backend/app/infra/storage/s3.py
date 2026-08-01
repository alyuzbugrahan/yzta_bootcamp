"""S3-compatible object storage (AWS S3, MinIO).

boto3 is synchronous, so every call is pushed to a worker thread. The client itself is created
once and reused: boto3 clients are thread-safe for calls, and building one per request costs a
credential resolution and a session setup each time.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import anyio.to_thread

from app.core.logging import get_logger
from app.infra.storage.base import ObjectNotFound, StorageError, validate_key

if TYPE_CHECKING:
    from app.config import StorageSettings

log = get_logger(__name__)


class S3Storage:
    def __init__(
        self,
        bucket: str,
        endpoint_url: str | None = None,
        region: str = "us-east-1",
        access_key: str | None = None,
        secret_key: str | None = None,
        presign_ttl: int = 300,
    ) -> None:
        try:
            import boto3
            from botocore.config import Config
        except ImportError as exc:  # pragma: no cover - depends on optional extra
            raise StorageError(
                "S3 storage requires boto3: pip install 'agrovision-backend[s3]'"
            ) from exc

        self._bucket = bucket
        self._presign_ttl = presign_ttl

        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            # Path addressing, because MinIO and most S3-compatible servers do not offer
            # per-bucket virtual hosts.
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        )

    @classmethod
    def from_settings(cls, settings: StorageSettings) -> S3Storage:
        return cls(
            bucket=settings.bucket,
            endpoint_url=settings.endpoint_url or None,
            region=settings.region,
            access_key=settings.access_key or None,
            secret_key=settings.secret_key or None,
            presign_ttl=settings.presign_ttl_seconds,
        )

    def ensure_bucket(self) -> None:
        """Create the bucket if it is missing. Blocking; call at startup."""
        from botocore.exceptions import ClientError

        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self._bucket)
            log.info("bucket_created", bucket=self._bucket)

    async def put(self, key: str, data: bytes, content_type: str = "image/jpeg") -> None:
        validate_key(key)
        await anyio.to_thread.run_sync(self._put, key, data, content_type)

    def _put(self, key: str, data: bytes, content_type: str) -> None:
        self._client.put_object(
            Bucket=self._bucket, Key=key, Body=data, ContentType=content_type
        )

    async def get(self, key: str) -> bytes:
        validate_key(key)
        return await anyio.to_thread.run_sync(self._get, key)

    def _get(self, key: str) -> bytes:
        from botocore.exceptions import ClientError

        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in {"NoSuchKey", "404"}:
                raise ObjectNotFound(key) from exc
            raise
        return response["Body"].read()

    async def exists(self, key: str) -> bool:
        validate_key(key)
        return await anyio.to_thread.run_sync(self._exists, key)

    def _exists(self, key: str) -> bool:
        from botocore.exceptions import ClientError

        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError:
            return False
        return True

    async def delete_prefix(self, prefix: str) -> int:
        return await anyio.to_thread.run_sync(self._delete_prefix, prefix)

    def _delete_prefix(self, prefix: str) -> int:
        """Paginated, because a long harvest can exceed the 1000-key delete limit."""
        paginator = self._client.get_paginator("list_objects_v2")
        deleted = 0

        for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
            keys = [{"Key": item["Key"]} for item in page.get("Contents", [])]
            if not keys:
                continue
            self._client.delete_objects(Bucket=self._bucket, Delete={"Objects": keys})
            deleted += len(keys)

        return deleted

    def presigned_url(self, key: str, expires_in: int | None = None) -> str | None:
        """A short-lived direct URL.

        Anyone holding the URL can fetch the object until it expires, so the TTL is minutes,
        not hours. It exists so image bytes do not have to be proxied through the API for
        every thumbnail in a farmer's history view.
        """
        validate_key(key)
        return self._client.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket, "Key": key},
            ExpiresIn=expires_in or self._presign_ttl,
        )
