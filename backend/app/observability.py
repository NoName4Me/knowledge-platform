from __future__ import annotations

import logging
import time
import uuid
from collections.abc import Awaitable, Callable

from starlette.types import Message, Receive, Scope, Send

logger = logging.getLogger("kp.http")


class RequestLogMiddleware:
    """记录请求元数据，不记录正文、查询参数、Cookie 或凭据。"""

    def __init__(self, app: Callable[[Scope, Receive, Send], Awaitable[None]]) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        started = time.perf_counter()
        status_code = 500

        async def send_with_request_id(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_request_id)
        except Exception:
            logger.exception(
                "request_id=%s method=%s path=%s status=500 duration_ms=%.1f",
                request_id,
                scope["method"],
                scope["path"],
                (time.perf_counter() - started) * 1000,
            )
            raise
        else:
            logger.info(
                "request_id=%s method=%s path=%s status=%d duration_ms=%.1f",
                request_id,
                scope["method"],
                scope["path"],
                status_code,
                (time.perf_counter() - started) * 1000,
            )
