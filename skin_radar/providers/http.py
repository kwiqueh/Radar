from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx


class APIError(RuntimeError):
    """A safe error without request URLs, API keys or server-supplied bodies."""


class ResilientHttpClient:
    def __init__(self, concurrency: int = 4, transport: httpx.AsyncBaseTransport | None = None):
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(25),
            follow_redirects=False,
            transport=transport,
            headers={"User-Agent": "SkinRadar/0.1 (+read-only market research)"},
        )
        self._semaphore = asyncio.Semaphore(concurrency)
        self._cooldowns: dict[str, float] = {}

    async def get_json(self, url: str, **kwargs: Any) -> Any:
        host = httpx.URL(url).host
        if self._cooldowns.get(host, 0) > time.monotonic():
            raise APIError(f"{host}: rate limit cooldown active")
        for attempt in range(3):
            try:
                async with self._semaphore:
                    response = await self._client.get(url, **kwargs)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt == 2:
                    raise APIError(f"{host}: {type(exc).__name__}") from None
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            except httpx.HTTPError as exc:
                raise APIError(f"{host}: {type(exc).__name__}") from None
            status = response.status_code
            if status == 429:
                delay = self._retry_after(response.headers.get("Retry-After"))
                self._cooldowns[host] = time.monotonic() + delay
                raise APIError(f"{host}: HTTP 429; wait at least {delay:.0f}s")
            if status >= 500 and attempt < 2:
                await asyncio.sleep(0.5 * 2**attempt)
                continue
            if not 200 <= status < 300:
                raise APIError(f"{host}: HTTP {status}")
            try:
                return response.json()
            except ValueError:
                raise APIError(f"{host}: invalid JSON") from None
        raise APIError(f"{host}: request attempts exhausted")

    @staticmethod
    def _retry_after(value: str | None) -> float:
        try:
            return max(60.0, float(value))
        except (ValueError, TypeError):
            try:
                stamp = parsedate_to_datetime(value)
                return max(60.0, (stamp - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError):
                return 370.0

    async def post(self, url: str, **kwargs: Any) -> httpx.Response:
        try:
            async with self._semaphore:
                response = await self._client.post(url, **kwargs)
                response.raise_for_status()
                payload = response.json()
                if payload.get("ok") is False:
                    raise APIError("Telegram API rejected the request")
                return response
        except (httpx.HTTPError, ValueError):
            raise APIError(
                "Telegram request failed; check token, chat ID and connectivity"
            ) from None

    async def close(self) -> None:
        await self._client.aclose()
