"""API error handling — the standard error envelope (API_CONTRACT.md §0).

The `code` values form a closed set for v1; do not invent new ones.
"""

from __future__ import annotations

from typing import Mapping, Optional

# Closed error-code set (API_CONTRACT.md §0) — kept as a constant so tests and
# handlers can assert membership.
ERROR_CODES = frozenset(
    {
        "invalid_request",
        "invalid_handedness",
        "invalid_object_key",
        "invalid_timestamp",
        "unauthorized",
        "job_not_found",
        "clip_not_found",
        "clip_too_large",
        "unprocessable_clip",
        "busy",
        "internal_error",
        "models_not_ready",
    }
)


class ApiError(Exception):
    """Raise from any route/dependency; rendered as the standard envelope."""

    def __init__(
        self,
        status_code: int,
        code: str,
        message: str,
        field: Optional[str] = None,
        headers: Optional[Mapping[str, str]] = None,
    ) -> None:
        assert code in ERROR_CODES, f"unknown error code: {code}"
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.field = field
        self.headers = dict(headers or {})


def error_envelope(code: str, message: str, field: Optional[str], request_id: str) -> dict:
    return {
        "error": {
            "code": code,
            "message": message,
            "field": field,
            "request_id": request_id,
        }
    }
