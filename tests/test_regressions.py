from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from skin_radar.config import Settings
from skin_radar.core.scanner import OpportunityScanner
from skin_radar.models import Confidence, QuoteKind
from skin_radar.service import RadarService


async def test_csv_filters_survive_restart(settings):
    service = RadarService(settings)
    service.update_filter("enabled_providers", "csfloat")
    service.update_filter("excluded_items", "Test item,Other item")
    await service.close()
    reopened = RadarService(settings)
    assert reopened.settings.enabled_providers == ("csfloat",)
    assert reopened.settings.excluded_items == ("Test item", "Other item")
    await reopened.close()


async def test_invalid_boolean_does_not_silently_change_filter(settings):
    service = RadarService(settings)
    with pytest.raises(ValueError):
        service.update_filter("allow_unknown_hold", "maybe")
    assert not service.settings.allow_unknown_hold
    await service.close()


async def test_uncertain_delivery_not_sent_again(settings, quotes):
    service = RadarService(settings)
    offer = OpportunityScanner(settings).scan(quotes)[0]
    service.notifier.send = AsyncMock(side_effect=RuntimeError("timeout"))
    await service._send_new_alerts([offer])
    await service._send_new_alerts([offer])
    service.notifier.send.assert_awaited_once()
    assert service.storage.alerts()[0]["status"] == "uncertain"
    await service.close()


def test_future_quote_rejected(settings, quotes):
    quotes[0].observed_at = datetime.now(UTC) + timedelta(days=1)
    assert not OpportunityScanner(settings).scan(quotes)


def test_trade_lock_cannot_be_ignored(settings, quotes):
    quotes[0].item.tradable_after = datetime.now(UTC) + timedelta(days=8)
    assert not OpportunityScanner(settings).scan(quotes)


def test_zero_sales_and_transfer_delay_not_confirmed(settings, quotes):
    quotes[1].kind = QuoteKind.BUY_ORDER
    quotes[1].volume_24h = quotes[1].sales_7d = 0
    result = OpportunityScanner(settings.model_copy(update={"min_liquidity_score": 0})).scan(quotes)
    assert result[0].confidence != Confidence.CONFIRMED


def test_env_example_loads_without_private_values():
    settings = Settings(_env_file=".env.example")
    assert settings.mode == "demo" and settings.fx_verified_at is None
    assert not settings.telegram_bot_token and not settings.csfloat_api_key
