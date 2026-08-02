from __future__ import annotations

import logging
from typing import Any

from fastapi import Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)


class AppError(Exception):
    def __init__(
        self,
        message: str,
        *,
        code: str = "application_error",
        status_code: int = 400,
        details: Any | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details


def error_body(code: str, message: str, details: Any | None = None) -> dict[str, Any]:
    error: dict[str, Any] = {"code": code, "message": message}
    if details is not None:
        error["details"] = details
    return {"error": error}


async def app_error_handler(_: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        content=error_body(exc.code, exc.message, exc.details),
        status_code=exc.status_code,
    )


async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    details = [
        {"field": ".".join(str(part) for part in item.get("loc", [])), "message": item.get("msg")}
        for item in exc.errors()
    ]
    return JSONResponse(
        content=error_body("validation_error", "请求参数无效", details),
        status_code=422,
    )


async def http_error_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    message = exc.detail if isinstance(exc.detail, str) else "请求失败"
    return JSONResponse(content=error_body("http_error", message), status_code=exc.status_code)


async def unexpected_error_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception("unhandled application error", exc_info=exc)
    return JSONResponse(
        content=error_body("internal_error", "服务器内部错误，请稍后重试"),
        status_code=500,
    )
