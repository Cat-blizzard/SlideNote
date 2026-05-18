from __future__ import annotations

import random
import re
import time
import urllib.error
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


@dataclass(slots=True)
class RetryResult(Generic[T]):
    value: T
    retries: int


def with_api_retries(
    call: Callable[[], T],
    *,
    max_retries: int = 2,
    base_delay: float = 0.5,
    jitter: float = 0.15,
) -> RetryResult[T]:
    attempts = 0
    while True:
        try:
            return RetryResult(value=call(), retries=attempts)
        except Exception as exc:
            if attempts >= max_retries or not is_transient_api_error(exc):
                raise
            delay = base_delay * (2**attempts) + random.uniform(0.0, jitter)
            attempts += 1
            time.sleep(delay)


def is_transient_api_error(exc: Exception) -> bool:
    if isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError)):
        return True
    status = _status_code(exc)
    if status is not None:
        return status in {408, 409, 425, 429} or 500 <= status <= 599
    name = exc.__class__.__name__.lower()
    if any(marker in name for marker in ("ratelimit", "timeout", "connection", "serviceunavailable", "internalserver")):
        return True
    message = str(exc).lower()
    if _http_status_in_message(message) in {408, 409, 425, 429, 500, 502, 503, 504}:
        return True
    transient_markers = (
        "rate limit",
        "ratelimit",
        "too many requests",
        "timed out",
        "timeout",
        "temporarily unavailable",
        "service unavailable",
        "connection reset",
        "connection aborted",
        "remote end closed connection",
    )
    return any(marker in message for marker in transient_markers)


def _status_code(exc: Exception) -> int | None:
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _http_status_in_message(message: str) -> int | None:
    match = re.search(r"\b(?:http\s*)?([45]\d\d)\b", message)
    if not match:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None
