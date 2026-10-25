from datetime import UTC, datetime

from skin_radar.config import VenueFees
from skin_radar.core.scanner import OpportunityScanner
from skin_radar.service import RadarService


async def test_reported_unaccepted_setup_shows_only_connection_step(settings):
    settings = settings.model_copy(update={"mode": "live"})
    service = RadarService(settings)
    warnings = service.configuration_warnings()
    assert len(warnings) == 1
    assert "Сохранить и проверить источники" in warnings[0]
    assert "FX_VERIFIED_AT" not in warnings[0] and "fees.local.json" not in warnings[0]
    assert not service.settings.api_terms_accepted
    await service.scan()
    assert not service._provider_cache  # No API permission was silently granted.
    await service.close()


async def test_catalog_only_needs_no_fee_or_fx_setup(settings):
    service = RadarService(
        settings.model_copy(
            update={
                "mode": "live",
                "api_terms_accepted": True,
                "enabled_providers": ("market_csgo",),
            }
        )
    )
    warnings = " ".join(service.configuration_warnings())
    assert "без настройки курсов и комиссий" in warnings
    assert "FX_VERIFIED_AT" not in warnings and "профиль комиссий не подтверждён" not in warnings
    assert "не участвуют" in warnings
    await service.close()


async def test_trade_readiness_is_explained_in_panel_terms(settings, quotes):
    service = RadarService(settings.model_copy(update={"mode": "live", "api_terms_accepted": True}))
    service._cached_quotes = quotes
    warnings = " ".join(service.configuration_warnings())
    assert "Я проверил указанные курсы сейчас" in warnings
    assert "Комиссии для расчёта прибыли" in warnings
    assert "market_csgo: " not in warnings
    await service.close()


def test_ruble_trade_calculation_does_not_need_foreign_exchange(settings, quotes):
    fees = VenueFees(verified=True, cashout_available=True, seller_pct=12)
    settings = settings.model_copy(
        update={
            "mode": "live",
            "fx_verified_at": None,
            "venue_fees": {"csfloat": fees, "skinport": fees},
        }
    )
    for quote in quotes:
        quote.currency = "RUB"
        quote.price = quote.price_rub
        quote.observed_at = datetime.now(UTC)
    assert OpportunityScanner(settings).scan(quotes)
