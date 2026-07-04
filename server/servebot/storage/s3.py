"""Real S3 backend (INFRA.md §2-3) — boto3 presigned PUT/GET.

STATUS: STUB / TODO — written to match INFRA.md but NOT exercised: this repo
is developed on a machine with no AWS credentials. Before first production
use:
  * provision the clips/meshes buckets + lifecycle rules (INFRA.md §2),
  * attach the least-privilege instance role (INFRA.md §3 IAM),
  * set SERVEBOT_STORAGE_BACKEND=s3 and the bucket/region env vars,
  * verify bucket CORS allows browser PUT/GET (INFRA.md §6),
  * TODO: add a `content-length-range` condition (presigned POST policy) or
    equivalent so S3 itself rejects oversized uploads [CONFIRM INFRA.md §3].
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Tuple

from .base import PresignedUpload, StorageBackend


class S3Storage(StorageBackend):
    def __init__(self, settings) -> None:
        try:
            import boto3  # not in requirements.txt on purpose (local dev is AWS-free)
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "S3 backend requires boto3 (pip install boto3) and AWS credentials; "
                "use SERVEBOT_STORAGE_BACKEND=local for development."
            ) from exc
        if not settings.s3_clips_bucket or not settings.s3_meshes_bucket:
            raise RuntimeError(
                "SERVEBOT_S3_CLIPS_BUCKET and SERVEBOT_S3_MESHES_BUCKET must be set."
            )
        self._s3 = boto3.client("s3", region_name=settings.aws_region or None)
        self._clips_bucket = settings.s3_clips_bucket
        self._meshes_bucket = settings.s3_meshes_bucket
        self._upload_ttl = settings.upload_url_ttl_s
        self._download_ttl = settings.glb_url_ttl_s

    def _bucket_for(self, object_key: str) -> str:
        return self._meshes_bucket if object_key.startswith("meshes/") else self._clips_bucket

    def mint_clip_upload(self, object_key: str, content_type: str) -> PresignedUpload:
        url = self._s3.generate_presigned_url(
            "put_object",
            Params={
                "Bucket": self._clips_bucket,
                "Key": object_key,
                "ContentType": content_type,
            },
            ExpiresIn=self._upload_ttl,
        )
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=self._upload_ttl)
        return PresignedUpload(
            object_key=object_key,
            url=url,
            method="PUT",
            headers={"Content-Type": content_type},
            expires_at=expires_at,
        )

    def object_exists(self, object_key: str) -> bool:
        try:
            self._s3.head_object(Bucket=self._bucket_for(object_key), Key=object_key)
            return True
        except self._s3.exceptions.ClientError:
            return False

    def get_object(self, object_key: str) -> bytes:
        try:
            resp = self._s3.get_object(Bucket=self._bucket_for(object_key), Key=object_key)
        except self._s3.exceptions.NoSuchKey as exc:
            raise KeyError(object_key) from exc
        return resp["Body"].read()

    def put_object(self, object_key: str, data: bytes, content_type: str) -> None:
        self._s3.put_object(
            Bucket=self._bucket_for(object_key),
            Key=object_key,
            Body=data,
            ContentType=content_type,
        )

    def mint_download(self, object_key: str) -> Tuple[str, datetime]:
        url = self._s3.generate_presigned_url(
            "get_object",
            Params={"Bucket": self._bucket_for(object_key), "Key": object_key},
            ExpiresIn=self._download_ttl,
        )
        return url, datetime.now(timezone.utc) + timedelta(seconds=self._download_ttl)
