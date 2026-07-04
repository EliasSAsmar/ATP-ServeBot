"""FastAPI app factory — wiring, middleware, and the standard error envelope.

Run locally:
    uvicorn servebot.main:app --host 127.0.0.1 --port 8000
"""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.middleware.base import BaseHTTPMiddleware

from . import api
from .config import Settings
from .errors import ApiError, error_envelope
from .jobs import JobStore, Worker
from .pipeline import StubAnalysisPipeline
from .storage import LocalDiskStorage, build_storage
from .storage.local import router as local_s3_router

logging.basicConfig(level=logging.INFO)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Every response carries X-Request-Id (echoed into error envelopes)."""

    async def dispatch(self, request: Request, call_next):
        request_id = request.headers.get("X-Request-Id") or str(uuid.uuid4())
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response


def _request_id(request: Request) -> str:
    return getattr(request.state, "request_id", None) or str(uuid.uuid4())


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings.from_env()

    storage = build_storage(settings)
    pipeline = StubAnalysisPipeline(storage=storage, settings=settings)
    job_store = JobStore()
    worker = Worker(job_store, pipeline, settings)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        await worker.start()
        yield
        await worker.stop()

    app = FastAPI(title="ATP-ServeBot API", version="v1", lifespan=lifespan)

    app.state.settings = settings
    app.state.storage = storage
    app.state.pipeline = pipeline
    app.state.job_store = job_store
    app.state.worker = worker
    app.state.minted_uploads = {}

    # Middleware (CORS outermost so even errors get CORS headers — INFRA.md §6).
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type", "X-API-Key", "X-Request-Id"],
        expose_headers=["X-Request-Id", "ETag"],
    )

    # ---- error envelope handlers (API_CONTRACT.md §0) ----

    @app.exception_handler(ApiError)
    async def handle_api_error(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(exc.code, exc.message, exc.field, _request_id(request)),
            headers=exc.headers,
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        field = None
        message = "Malformed JSON or missing required field."
        if errors:
            # Keep only named fields (skip "body" and positional indices, e.g.
            # the character offset reported for malformed JSON).
            loc = [p for p in errors[0].get("loc", ()) if isinstance(p, str) and p != "body"]
            field = ".".join(loc) or None
            message = f"Invalid request: {errors[0].get('msg', 'validation error')}" + (
                f" (field: {field})" if field else ""
            )
        return JSONResponse(
            status_code=400,
            content=error_envelope("invalid_request", message, field, _request_id(request)),
        )

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # Routing-level errors (unknown path, bad method) mapped onto the
        # closed code set: 4xx -> invalid_request, 5xx -> internal_error.
        code = "invalid_request" if exc.status_code < 500 else "internal_error"
        return JSONResponse(
            status_code=exc.status_code,
            content=error_envelope(code, str(exc.detail), None, _request_id(request)),
        )

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
        logging.getLogger("servebot").exception("unhandled error")
        return JSONResponse(
            status_code=500,
            content=error_envelope(
                "internal_error", "Unexpected server fault.", None, _request_id(request)
            ),
        )

    # ---- routes ----

    app.include_router(api.router)
    if isinstance(storage, LocalDiskStorage):
        # Local stand-in for S3 (presigned PUT/GET) — outside /v1 auth.
        app.include_router(local_s3_router)

    return app


app = create_app()
