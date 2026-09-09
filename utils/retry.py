"""Small retry helper for flaky network/browser operations."""
from __future__ import annotations

import time
from functools import wraps
from typing import Callable, TypeVar

from utils.logging import get_logger

log = get_logger()

T = TypeVar("T")


def retry(times: int = 3, delay_seconds: float = 2.0, exceptions: tuple = (Exception,)):
    def decorator(fn: Callable[..., T]) -> Callable[..., T]:
        @wraps(fn)
        def wrapper(*args, **kwargs):
            last_exc = None
            for attempt in range(1, times + 1):
                try:
                    return fn(*args, **kwargs)
                except exceptions as exc:  # noqa: PERF203
                    last_exc = exc
                    if attempt < times:
                        log.warning(f"[RETRY] {fn.__name__} attempt {attempt}/{times} failed: {exc}")
                        time.sleep(delay_seconds * attempt)
            raise last_exc
        return wrapper
    return decorator
