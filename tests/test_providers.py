from unittest.mock import AsyncMock

import httpx
import pytest

from skin_radar.providers.csfloat import CSFloatProvider
from skin_radar.providers.http import APIError, ResilientHttpClient
from skin_radar.providers.skinport import SkinportProvider


@pytest.mark.parametrize("status", [401, 403, 404])
async def test_api_auth_errors_not_retried(status):
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "denied"})

    client = ResilientHttpClient(transport=httpx.MockTransport(handler))
    with pytest.raises(APIError, match=f"HTTP {status}"):
        await client.get_json("https://example.com/?key=secret")
    assert len(calls) == 1
    await client.close()


async def test_rate_limit_retry_after_is_respected():
    calls = []

    async def handler(request):
        calls.append(request)
        return httpx.Response(429, headers={"Retry-After": "600"})

    client = ResilientHttpClient(transport=httpx.MockTransport(handler))
    with pytest.raises(APIError, match="600"):
        await client.get_json("https://example.com")
    with pytest.raises(APIError, match="cooldown"):
        await client.get_json("https://example.com")
    assert len(calls) == 1
    await client.close()


async def test_server_failure_recovers(monkeypatch):
    monkeypatch.setattr("skin_radar.providers.http.asyncio.sleep", AsyncMock())
    count = 0

    async def handler(request):
        nonlocal count
        count += 1
        return httpx.Response(503 if count == 1 else 200, json=[])

    client = ResilientHttpClient(transport=httpx.MockTransport(handler))
    assert await client.get_json("https://example.com") == []
    assert count == 2
    await client.close()


async def test_invalid_json_is_error():
    client = ResilientHttpClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, text="not JSON"))
    )
    with pytest.raises(APIError, match="invalid JSON"):
        await client.get_json("https://example.com")
    await client.close()


async def test_csfloat_cents_attributes_not_listing_age(settings):
    client = AsyncMock()
    client.get_json.return_value = {
        "data": [
            {
                "id": "123",
                "price": 1700,
                "created_at": "2020-01-01T00:00:00Z",
                "item": {
                    "market_hash_name": "AK-47 | Redline (Field-Tested)",
                    "float_value": 0.17,
                    "paint_seed": 42,
                    "stickers": [{"name": "Sticker"}],
                },
            }
        ]
    }
    quotes = (await CSFloatProvider(settings, client).fetch()).quotes
    assert quotes[0].price == 17
    assert quotes[0].price_rub == 1530
    assert quotes[0].item.paint_seed == 42
    assert quotes[0].observed_at.year >= 2026
    assert quotes[0].listing_created_at.year == 2020
    assert quotes[0].transfer_delay_hours is None


async def test_skinport_asks_are_not_sales_or_specific_listing(settings):
    client = AsyncMock()
    client.get_json.return_value = [
        {
            "market_hash_name": "AK-47 | Redline (Field-Tested)",
            "min_price": 25,
            "quantity": 15,
            "market_page": "https://skinport.com/market/730",
        }
    ]
    quote = (await SkinportProvider(settings, client).fetch()).quotes[0]
    assert quote.price_rub == 2250
    assert quote.aggregate
    assert quote.listing_count == 15
    assert quote.volume_24h is None and quote.sales_7d is None and quote.seller_count is None


async def test_unexpected_schema_not_silent_success(settings):
    client = AsyncMock()
    client.get_json.return_value = {"error": "schema changed"}
    with pytest.raises(ValueError):
        await CSFloatProvider(settings, client).fetch()
    with pytest.raises(ValueError):
        await SkinportProvider(settings, client).fetch()
