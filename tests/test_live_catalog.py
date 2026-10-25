from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from skin_radar.core.scanner import OpportunityScanner
from skin_radar.dashboard import create_app
from skin_radar.local_settings import SetupUpdate, load_local_settings
from skin_radar.models import QuoteKind
from skin_radar.providers.base import ProviderResult
from skin_radar.providers.http import APIError
from skin_radar.providers.market_csgo import MarketCSGOProvider
from skin_radar.service import RadarService


def catalog_payload(timestamp=None):
    return {
        "success": True,
        "time": timestamp or int(datetime.now(UTC).timestamp()),
        "currency": "RUB",
        "items": [
            {
                "market_hash_name": "AK-47 | Redline (Field-Tested)",
                "volume": "25",
                "price": "900.00",
            },
            {
                "market_hash_name": "StatTrak™ AK-47 | Redline (Field-Tested)",
                "volume": "99",
                "price": "1900.00",
            },
            {"market_hash_name": "invalid", "price": "NaN"},
        ],
    }


async def test_market_real_schema_reference_not_bid(settings, quotes):
    client = AsyncMock()
    client.get_json.return_value = catalog_payload()
    result = await MarketCSGOProvider(client).fetch()
    assert len(result.quotes) == 2
    quote = result.quotes[0]
    assert quote.kind == QuoteKind.CATALOG_REFERENCE
    assert quote.price_rub == Decimal("900")
    assert quote.currency == "RUB" and not quote.demo
    assert quote.volume_24h is None and quote.sales_7d is None and quote.listing_count is None
    # An inexpensive catalog reference must never become a buy offer or a sell bid.
    assert not OpportunityScanner(settings).scan([quote, quotes[1]])


async def test_source_timestamp_preserved_when_old():
    stamp = int((datetime.now(UTC) - timedelta(days=1)).timestamp())
    client = AsyncMock()
    client.get_json.return_value = catalog_payload(stamp)
    quote = (await MarketCSGOProvider(client).fetch()).quotes[0]
    assert int(quote.observed_at.timestamp()) == stamp


@pytest.mark.parametrize(
    "change",
    [
        {"success": False},
        {"currency": "USD"},
        {"time": None},
        {"time": True},
        {"items": {}},
        {"items": [{"unexpected": 1}]},
    ],
)
async def test_catalog_schema_drift_rejected(change):
    client = AsyncMock()
    client.get_json.return_value = {**catalog_payload(), **change}
    with pytest.raises(ValueError):
        await MarketCSGOProvider(client).fetch()


def test_setup_api_masks_secret_persists_restart_and_separates_modes(settings):
    service = RadarService(settings)
    with TestClient(create_app(service), base_url="http://127.0.0.1") as client:
        client.post("/api/scan")
        assert client.get("/api/quotes").json()["total"] == 3
        secret = "offline-example-key-never-a-real-credential"
        response = client.post(
            "/api/setup",
            json={
                "mode": "live",
                "csfloat_api_key": secret,
                "api_terms_accepted": False,
                "enabled_providers": ["market_csgo"],
            },
        )
        assert response.status_code == 200 and secret not in response.text
        assert response.json()["csfloat_key_set"]
        assert client.get("/health").json()["mode"] == "live"
        assert client.get("/api/quotes").json()["total"] == 0
        assert client.get("/api/opportunities").json() == []
        invalid = client.post("/api/setup", json={"csfloat_api_key": secret, "mode": "invalid"})
        assert invalid.status_code == 422 and secret not in invalid.text
    restored = load_local_settings(settings)
    assert restored.mode == "live" and restored.csfloat_api_key == secret
    assert restored.enabled_providers == ("market_csgo",)
    assert settings.local_config_file.stat().st_mode & 0o077 == 0 or __import__("os").name == "nt"


async def test_apply_settings_does_not_bypass_request_cooldown(settings, quotes):
    service = RadarService(settings)
    provider = AsyncMock()
    provider.name = "test-source"
    provider.fetch.return_value = ProviderResult(quotes, "ok")
    service.providers = lambda: [provider]
    await service.scan()
    await service.apply_setup(SetupUpdate(usd_rub="91"))
    await service.scan()
    assert provider.fetch.await_count == 1
    assert service.storage.quote_page()["total"] == 2
    assert len(service.storage.history(quotes[0].item.market_hash_name)) == 4
    await service.apply_setup(SetupUpdate(usd_rub="91"))
    await service.scan()
    assert len(service.storage.history(quotes[0].item.market_hash_name)) == 4
    await service.close()


async def test_save_failure_does_not_change_active_mode(settings, monkeypatch):
    service = RadarService(settings)

    def fail(*args):
        raise OSError("disk is read-only")

    monkeypatch.setattr("skin_radar.service.save_local_settings", fail)
    with pytest.raises(OSError):
        await service.apply_setup(SetupUpdate(mode="live"))
    assert service.settings.mode == "demo"
    await service.close()


def test_catalog_search_paging_and_cache_preserves_quotes(settings):
    service = RadarService(settings)
    with TestClient(create_app(service), base_url="http://127.0.0.1") as client:
        client.post("/api/scan")
        assert client.get("/api/quotes", params={"search": "Redline"}).json()["total"] == 2
        assert client.get("/api/quotes", params={"max_price_rub": 1150}).json()["total"] == 1
        assert client.get("/api/quotes", params={"search": "%"}).json()["total"] == 0
        page = client.get("/api/quotes", params={"limit": 1, "offset": 1}).json()
        assert len(page["items"]) == 1 and page["total"] == 3
        client.post("/api/scan")
        assert client.get("/api/quotes").json()["total"] == 3
        assert client.get("/api/stats").json()["latest_scan"]["quote_count"] == 3
        assert (
            client.post(
                "/api/setup", json={"mode": "live"}, headers={"Origin": "https://evil.example"}
            ).status_code
            == 403
        )


async def test_live_fee_profiles_do_not_rewrite_demo_numbers(settings):
    service = RadarService(settings)
    before = (await service.scan())[0].costs.net_profit_rub
    await service.apply_setup(SetupUpdate(venue_fees={"csfloat": {}, "skinport": {}}))
    after = (await service.scan())[0].costs.net_profit_rub
    assert before == after == Decimal("191.12")
    await service.close()


async def test_source_selection_survives_same_mode_setup(settings):
    service = RadarService(settings)
    service.update_filter("enabled_providers", "skinport")
    await service.apply_setup(SetupUpdate(enabled_providers=["market_csgo"]))
    await service.close()
    reopened = RadarService(load_local_settings(settings))
    assert reopened.settings.enabled_providers == ("market_csgo",)
    await reopened.close()


def test_old_opportunities_are_not_shown_as_current(settings):
    service = RadarService(settings)
    with TestClient(create_app(service), base_url="http://127.0.0.1") as client:
        client.post("/api/scan")
        assert client.get("/api/opportunities").json()
        for quote in service._cached_quotes:
            quote.observed_at = datetime.now(UTC) - timedelta(days=1)
        assert client.get("/api/opportunities").json() == []


async def test_auth_denial_waits_for_changed_key(settings, quotes):
    import time

    service = RadarService(settings)
    provider = AsyncMock()
    provider.name = "csfloat"
    provider.fetch.side_effect = APIError("csfloat.com: HTTP 403")
    with pytest.raises(APIError):
        await service._fetch_provider(provider)
    error = service._provider_cache["csfloat"][1]
    service._provider_cache["csfloat"] = (time.monotonic() - 1000, error)
    with pytest.raises(APIError):
        await service._fetch_provider(provider)
    assert provider.fetch.await_count == 1
    await service.apply_setup(SetupUpdate(csfloat_api_key="offline-test-new-key"))
    provider.fetch.side_effect = None
    provider.fetch.return_value = ProviderResult(quotes, "ok")
    assert (await service._fetch_provider(provider)).quotes
    assert provider.fetch.await_count == 2
    await service.close()
