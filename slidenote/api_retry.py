from __future__ import annotations

import random
import re
import time
import urllib.error
from dataclasses import dataclass
from typing import Callable, Generic, TypeVar

T = TypeVar("T")

DEFAULT_MAX_RETRIES = 2
TRANSIENT_STATUS_CODES = frozenset({408, 409, 425, 429})
# A status code only counts when it appears next to an HTTP/status marker, so
# numbers such as token counts or model names ("gpt-4o-2024-05-13") never match.
_STATUS_IN_MESSAGE = re.compile(r"\b(?:https?|status(?:[\s_]+code)?|error[\s_]+code|code)[\s:=]*([1-5]\d\d)\b")


@dataclass(slots=True)
class RetryResult(Generic[T]):
    value: T
    retries: int


def with_api_retries(
    call: Callable[[], T],
    *,
    max_retries: int = DEFAULT_MAX_RETRIES,
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


def is_transient_status(status: int) -> bool:
    return status in TRANSIENT_STATUS_CODES or 500 <= status <= 599


def is_transient_api_error(exc: BaseException) -> bool:
    # HTTPError subclasses URLError, so its status must be checked first:
    # 400/401/403/404 are permanent and must not be retried.
    if isinstance(exc, urllib.error.HTTPError):
        return is_transient_status(exc.code)
    if isinstance(exc, (TimeoutError, ConnectionError, urllib.error.URLError)):
        return True
    cause = exc.__cause__
    if isinstance(cause, (urllib.error.URLError, TimeoutError, ConnectionError)):
        # Providers wrap urllib errors in RuntimeError; classify the original.
        return is_transient_api_error(cause)
    status = _status_code(exc)
    if status is not None:
        return is_transient_status(status)
    name = exc.__class__.__name__.lower()
    if any(marker in name for marker in ("ratelimit", "timeout", "connection", "serviceunavailable", "internalserver")):
        return True
    message = str(exc).lower()
    message_status = _http_status_in_message(message)
    if message_status is not None:
        return is_transient_status(message_status)
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


def _status_code(exc: BaseException) -> int | None:
    for attr in ("status_code", "status", "code"):
        value = getattr(exc, attr, None)
        if isinstance(value, int) and not isinstance(value, bool):
            return value
    response = getattr(exc, "response", None)
    value = getattr(response, "status_code", None)
    return value if isinstance(value, int) else None


def _http_status_in_message(message: str) -> int | None:
    match = _STATUS_IN_MESSAGE.search(message)
    return int(match.group(1)) if match else None
