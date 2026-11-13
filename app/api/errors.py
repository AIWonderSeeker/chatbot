from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import request_id_var

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str) -> None:
        super().__init__(code)
        self.status_code = status_code
        self.code = code
        self.message = message


def _request_id(request: Request | None) -> str:
    # Handlers for unhandled exceptions run outside RequestContextMiddleware,
    # after the context var is reset, so prefer the value stored on the scope.
    if request is not None:
        rid = request.scope.get("state", {}).get("request_id")
        if rid:
            return str(rid)
    return request_id_var.get()


def error_response(status_code: int, code: str, message: str, request: Request | None = None) -> JSONResponse:
    rid = _request_id(request)
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message}, "request_id": rid},
        headers={"x-request-id": rid},
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(request: Request, exc: ApiError) -> JSONResponse:
        return error_response(exc.status_code, exc.code, exc.message, request)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        # Report which fields failed, never the submitted values (could be PHI).
        fields = sorted({".".join(str(p) for p in e.get("loc", [])[1:]) or "body" for e in exc.errors()})
        return error_response(422, "invalid_request", f"Invalid request fields: {', '.join(fields)}", request)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
        error_code = code.get(exc.status_code, "http_error")
        return error_response(exc.status_code, error_code, str(exc.detail), request)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error")
        return error_response(500, "internal_error", "Something went wrong. Try again.", request)
