"""全局速率限制器 - 防止B站API限流"""

import asyncio
import time
from typing import Optional


class RateLimiter:
    """令牌桶式速率限制器，全局单例使用

    用法:
        limiter = RateLimiter(rps=1.0)  # 每秒最多1个请求
        await limiter.acquire()         # 等待直到可以发请求
        # ... 发起HTTP请求 ...
    """

    def __init__(self, rps: float = 0.8, burst: int = 3):
        """初始化

        Args:
            rps: 每秒最大请求数 (默认0.8 = 每1.25秒一个请求)
            burst: 允许的突发请求数 (短时间内可连续发的最大请求数)
        """
        self._rps: float = rps
        self._interval: float = 1.0 / rps
        self._burst: int = max(burst, 1)
        self._tokens: float = float(burst)
        self._last_refill: float = time.monotonic()
        self._lock: Optional[asyncio.Lock] = None

    async def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    async def acquire(self) -> None:
        """获取一个请求许可（会阻塞等待）"""
        lock = await self._get_lock()
        async with lock:
            now = time.monotonic()
            elapsed = now - self._last_refill
            # 补充令牌
            self._tokens = min(
                self._burst,
                self._tokens + elapsed * self._rps,
            )
            self._last_refill = now

            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return

            # 需要等待
            wait_time = (1.0 - self._tokens) * self._interval
            await asyncio.sleep(wait_time)
            self._tokens = 0.0
            self._last_refill = time.monotonic()

    @property
    def rps(self) -> float:
        return self._rps

    @property
    def interval(self) -> float:
        return self._interval


# 全局实例：默认每秒最多0.8个请求（约1.25秒/请求），突发3个
_global_limiter: Optional[RateLimiter] = None


def get_limiter(rps: float = 0.8, burst: int = 3) -> RateLimiter:
    """获取或创建全局速率限制器"""
    global _global_limiter
    if _global_limiter is None:
        _global_limiter = RateLimiter(rps=rps, burst=burst)
    return _global_limiter


def reset_limiter() -> None:
    """重置全局速率限制器（用于测试）"""
    global _global_limiter
    _global_limiter = None
