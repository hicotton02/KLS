from __future__ import annotations

import httpx

from app.http_retry import get_source_with_retries, get_with_retries, retry_after_seconds


def test_get_with_retries_honors_429_retry_after() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"retry-after": "2.5"}, request=request)
        return httpx.Response(200, text="ok", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        response = get_with_retries(client, "https://example.test/resource", sleeper=sleeps.append)
    finally:
        client.close()

    assert response.status_code == 200
    assert calls == 2
    assert sleeps == [2.5]


def test_retry_after_seconds_ignores_invalid_values() -> None:
    assert retry_after_seconds("not a date") is None


def test_source_retries_wait_through_a_long_429_window() -> None:
    calls = 0
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls < 8:
            return httpx.Response(429, request=request)
        return httpx.Response(200, text="ok", request=request)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    try:
        response = get_source_with_retries(client, "https://example.test/resource", sleeper=sleeps.append)
    finally:
        client.close()

    assert response.status_code == 200
    assert calls == 8
    assert sleeps == [5.0, 10.0, 20.0, 40.0, 80.0, 120.0, 120.0]
