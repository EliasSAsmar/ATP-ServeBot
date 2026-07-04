"""Blob storage abstraction (INFRA.md §2-3).

`StorageBackend` is the seam: routes and the pipeline only ever talk to it.
`LocalDiskStorage` makes the full client flow work with zero AWS; the boto3
`S3Storage` (storage/s3.py) is the real implementation, wired but untested
until deployment.
"""

from .base import PresignedUpload, StorageBackend
from .local import LocalDiskStorage

__all__ = ["PresignedUpload", "StorageBackend", "LocalDiskStorage", "build_storage"]


def build_storage(settings) -> StorageBackend:
    if settings.storage_backend == "local":
        return LocalDiskStorage(settings)
    if settings.storage_backend == "s3":
        from .s3 import S3Storage  # deferred: boto3 only needed in prod

        return S3Storage(settings)
    raise ValueError(f"unknown storage backend: {settings.storage_backend!r}")
