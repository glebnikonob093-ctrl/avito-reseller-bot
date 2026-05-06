from __future__ import annotations

import asyncio
import time


class SimpleRateLimiter:
    """
    Very small async token bucket-ish limiter.
    Good enough for MVP; replace with a robust limiter if needed.
    """

    def __init__(self, max_per_minute: int) -> None:
        self._min_interval = 60.0 / max(1, max_per_minute)
        self._lock = asyncio.Lock()
        self._last = 0.0

    async def wait(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait_for = self._min_interval - (now - self._last)
            if wait_for > 0:
                await asyncio.sleep(wait_for)
            self._last = time.monotonic()

