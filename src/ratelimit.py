import threading
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class RateLimiter:
    """Sliding-window request limit per client IP, used as a FastAPI
    dependency. In-memory, so it is per process; behind a reverse proxy run
    uvicorn with --proxy-headers so the client IP is the real one."""

    def __init__(self, limit: int, window_seconds: float = 60.0):
        self.limit = limit
        self.window = window_seconds
        self._hits = defaultdict(deque)
        self._lock = threading.Lock()

    def __call__(self, request: Request):
        if self.limit <= 0:
            return
        key = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] > self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                retry_after = int(self.window - (now - hits[0])) + 1
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many requests - try again in {retry_after}s",
                    headers={"Retry-After": str(retry_after)},
                )
            hits.append(now)
