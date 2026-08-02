from __future__ import annotations

import logging
import time
from collections.abc import Awaitable, Callable

from starlette.requests import Request
from starlette.responses import Response


def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=False,
    )


async def request_logging_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    logger = logging.getLogger("urus.request")
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.exception("request failed path=%s duration_ms=%.1f", request.url.path, elapsed_ms)
        raise
    elapsed_ms = (time.perf_counter() - started) * 1000
    logger.info(
        "request method=%s path=%s status=%s duration_ms=%.1f",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response

