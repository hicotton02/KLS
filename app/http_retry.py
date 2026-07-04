from __future__ import annotations

import email.utils
import time
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

import httpx
import requests

from app.text_utils import clean_text


RETRY_STATUS_CODES = {429, 500, 502, 503, 504}
RETRY_EXCEPTIONS = (httpx.TimeoutException, httpx.TransportError, requests.exceptions.RequestException)


def retry_after_seconds(value: str | None, *, now: datetime | None = None) -> float | None:
    raw = clean_text(value)
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass

    try:
        parsed = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    reference = now or datetime.now(timezone.utc)
    return max(0.0, (parsed - reference).total_seconds())


def request_with_retries(
    client: Any,
    method: str,
    url: str,
    *,
    max_attempts: int = 6,
    retry_status_codes: set[int] | None = None,
    base_delay_seconds: float = 1.0,
    max_delay_seconds: float = 30.0,
    sleeper: Callable[[float], None] = time.sleep,
    **kwargs: Any,
) -> Any:
    statuses = retry_status_codes or RETRY_STATUS_CODES
    attempts = max(1, int(max_attempts))
    last_error: Exception | None = None

    for attempt in range(attempts):
        try:
            requester = getattr(client, "request", None)
            if requester is not None:
                response = requester(method, url, **kwargs)
            else:
                response = getattr(client, method.lower())(url, **kwargs)
        except RETRY_EXCEPTIONS as exc:
            last_error = exc
            if attempt >= attempts - 1:
                raise
            _sleep_before_retry(attempt, base_delay_seconds, max_delay_seconds, sleeper)
            continue

        if response.status_code not in statuses or attempt >= attempts - 1:
            return response

        retry_after = retry_after_seconds(response.headers.get("retry-after"))
        if retry_after is None:
            retry_after = min(max_delay_seconds, base_delay_seconds * (2**attempt))
        sleeper(max(0.0, retry_after))

    if last_error is not None:
        raise last_error
    raise RuntimeError("HTTP request retry loop ended without a response")


def get_with_retries(client: Any, url: str, **kwargs: Any) -> Any:
    return request_with_retries(client, "GET", url, **kwargs)


def post_with_retries(client: Any, url: str, **kwargs: Any) -> Any:
    return request_with_retries(client, "POST", url, **kwargs)


def _sleep_before_retry(
    attempt: int,
    base_delay_seconds: float,
    max_delay_seconds: float,
    sleeper: Callable[[float], None],
) -> None:
    delay = min(max_delay_seconds, base_delay_seconds * (2**attempt))
    sleeper(max(0.0, delay))
