"""Local-dev storage backend — S3 semantics on local disk, zero AWS.

Objects live under ``settings.local_storage_dir``. "Presigned" URLs point at
the ``/local-s3/{object_key}`` routes (mounted in main.py, deliberately
OUTSIDE the /v1 auth boundary, mirroring S3's signature-not-API-key model)
and carry an HMAC token covering method, key, content type, and expiry.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Tuple
from urllib.parse import quote

from fastapi import APIRouter, Request, Response

from .base import PresignedUpload, StorageBackend

_EXT_CONTENT_TYPES = {
    ".glb": "model/gltf-binary",
    ".webm": "video/webm",
    ".mp4": "video/mp4",
}


class LocalDiskStorage(StorageBackend):
    def __init__(self, settings) -> None:
        self._root = Path(settings.local_storage_dir).resolve()  # created lazily on write
        self._secret = settings.signing_secret.encode("utf-8")
        self._base_url = settings.public_base_url.rstrip("/")
        self._upload_ttl = settings.upload_url_ttl_s
        self._download_ttl = settings.glb_url_ttl_s
        self._max_bytes = settings.max_clip_bytes

    # -- token "presigning" -------------------------------------------------

    def _sign(self, method: str, object_key: str, content_type: str, expires: int) -> str:
        payload = f"{method}\n{object_key}\n{content_type}\n{expires}".encode("utf-8")
        return hmac.new(self._secret, payload, hashlib.sha256).hexdigest()

    def verify_token(
        self, method: str, object_key: str, content_type: str, expires: int, token: str
    ) -> bool:
        expected = self._sign(method, object_key, content_type, expires)
        return hmac.compare_digest(expected, token)

    def _url(self, method: str, object_key: str, content_type: str, ttl_s: int) -> Tuple[str, datetime]:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=ttl_s)
        expires = int(expires_at.timestamp())
        token = self._sign(method, object_key, content_type, expires)
        url = (
            f"{self._base_url}/local-s3/{quote(object_key)}"
            f"?expires={expires}&token={token}"
        )
        return url, expires_at

    # -- paths ---------------------------------------------------------------

    def _path(self, object_key: str) -> Path:
        path = (self._root / object_key).resolve()
        if not path.is_relative_to(self._root):  # traversal guard
            raise KeyError(object_key)
        return path

    def _meta_path(self, object_key: str) -> Path:
        return self._path(object_key).with_suffix(
            self._path(object_key).suffix + ".meta.json"
        )

    def content_type_of(self, object_key: str) -> str:
        meta = self._meta_path(object_key)
        if meta.exists():
            return json.loads(meta.read_text())["content_type"]
        return _EXT_CONTENT_TYPES.get(Path(object_key).suffix, "application/octet-stream")

    # -- StorageBackend ------------------------------------------------------

    def mint_clip_upload(self, object_key: str, content_type: str) -> PresignedUpload:
        url, expires_at = self._url("PUT", object_key, content_type, self._upload_ttl)
        return PresignedUpload(
            object_key=object_key,
            url=url,
            method="PUT",
            headers={"Content-Type": content_type},
            expires_at=expires_at,
        )

    def object_exists(self, object_key: str) -> bool:
        try:
            return self._path(object_key).is_file()
        except KeyError:
            return False

    def get_object(self, object_key: str) -> bytes:
        path = self._path(object_key)
        if not path.is_file():
            raise KeyError(object_key)
        return path.read_bytes()

    def put_object(self, object_key: str, data: bytes, content_type: str) -> None:
        path = self._path(object_key)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        self._meta_path(object_key).write_text(json.dumps({"content_type": content_type}))

    def mint_download(self, object_key: str) -> Tuple[str, datetime]:
        return self._url("GET", object_key, "", self._download_ttl)

    @property
    def max_bytes(self) -> int:
        return self._max_bytes


# --------------------------------------------------------------------------
# /local-s3 routes — the local stand-in for S3 itself. Raw responses (no
# error envelope, no X-API-Key), matching API_CONTRACT.md §0's carve-out for
# raw S3 responses.
# --------------------------------------------------------------------------

router = APIRouter(prefix="/local-s3", include_in_schema=False)


def _storage(request: Request) -> LocalDiskStorage:
    storage = request.app.state.storage
    assert isinstance(storage, LocalDiskStorage), "/local-s3 requires local storage"
    return storage


def _check(request: Request, storage: LocalDiskStorage, method: str, key: str, ct: str):
    try:
        expires = int(request.query_params.get("expires", "0"))
    except ValueError:
        expires = 0
    token = request.query_params.get("token", "")
    if not storage.verify_token(method, key, ct, expires, token):
        return Response("SignatureDoesNotMatch", status_code=403, media_type="text/plain")
    if datetime.now(timezone.utc).timestamp() > expires:
        return Response("Request has expired", status_code=403, media_type="text/plain")
    return None


@router.put("/{object_key:path}")
async def local_s3_put(object_key: str, request: Request) -> Response:
    storage = _storage(request)
    content_type = request.headers.get("content-type", "")
    denied = _check(request, storage, "PUT", object_key, content_type)
    if denied:
        return denied
    body = await request.body()
    if len(body) > storage.max_bytes:  # S3 content-length-range equivalent
        return Response("EntityTooLarge", status_code=413, media_type="text/plain")
    storage.put_object(object_key, body, content_type)
    etag = hashlib.md5(body).hexdigest()
    return Response(status_code=200, headers={"ETag": f'"{etag}"'})


@router.get("/{object_key:path}")
async def local_s3_get(object_key: str, request: Request) -> Response:
    storage = _storage(request)
    denied = _check(request, storage, "GET", object_key, "")
    if denied:
        return denied
    try:
        data = storage.get_object(object_key)
    except KeyError:
        return Response("NoSuchKey", status_code=404, media_type="text/plain")
    return Response(content=data, media_type=storage.content_type_of(object_key))
