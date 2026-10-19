from __future__ import annotations

import math
import threading
import time
from collections import deque
from collections.abc import Callable

from fastapi import HTTPException, status


class RateLimitResult:
    def __init__(self, allowed: bool, retry_after: int = 0) -> None:
        self.allowed = allowed
        self.retry_after = retry_after


class FixedWindowLimiter:
    """进程内固定窗口限流；适用于当前单 API 进程的本地部署。"""

    def __init__(
        self,
        limit: int,
        window_seconds: int,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1 or window_seconds < 1:
            raise ValueError("限流阈值和窗口必须大于零")
        self.limit = limit
        self.window_seconds = window_seconds
        self._clock = clock
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> RateLimitResult:
        now = self._clock()
        cutoff = now - self.window_seconds
        with self._lock:
            bucket = self._hits.setdefault(key, deque())
            while bucket and bucket[0] <= cutoff:
                bucket.popleft()
            if len(bucket) >= self.limit:
                retry_after = max(1, math.ceil(self.window_seconds - (now - bucket[0])))
                return RateLimitResult(False, retry_after)
            bucket.append(now)
            return RateLimitResult(True)

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


def enforce_rate_limit(limiter: FixedWindowLimiter, key: str) -> None:
    result = limiter.check(key)
    if result.allowed:
        return
    raise HTTPException(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        detail="请求过于频繁，请稍后再试",
        headers={"Retry-After": str(result.retry_after)},
    )
