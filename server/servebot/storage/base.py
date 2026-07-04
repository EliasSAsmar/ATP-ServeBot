"""Storage interface — everything above this layer is AWS-agnostic."""

from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Tuple


@dataclass(frozen=True)
class PresignedUpload:
    object_key: str
    url: str
    method: str  # always "PUT" in v1
    headers: Dict[str, str]
    expires_at: datetime


class StorageBackend(abc.ABC):
    """Presigned-URL blob store (S3 semantics, INFRA.md §3)."""

    @abc.abstractmethod
    def mint_clip_upload(self, object_key: str, content_type: str) -> PresignedUpload:
        """Presigned PUT for a clip upload (no API key on the PUT itself)."""

    @abc.abstractmethod
    def object_exists(self, object_key: str) -> bool:
        """HEAD-equivalent existence check (used by POST /v1/serves)."""

    @abc.abstractmethod
    def get_object(self, object_key: str) -> bytes:
        """Fetch object bytes (worker download). Raises KeyError if missing."""

    @abc.abstractmethod
    def put_object(self, object_key: str, data: bytes, content_type: str) -> None:
        """Server-side write (worker uploads the GLB mesh)."""

    @abc.abstractmethod
    def mint_download(self, object_key: str) -> Tuple[str, datetime]:
        """Presigned GET url + expiry (mesh `glb_url` / `glb_expires_at`)."""
