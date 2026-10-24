from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError

from skin_radar.config import Settings, VenueFees
from skin_radar.core.matching import can_compare, identity_from_market_hash_name
from skin_radar.core.profit import calculate_costs
from skin_radar.core.scanner import OpportunityScanner
from skin_radar.models import Confidence, Liquidity, QuoteKind


def test_profit_all_costs_and_roi(quotes, settings):
    settings = settings.model_copy(update={"fixed_cost_rub": Decimal("20")})
    result = calculate_costs(*quotes, settings)
    assert result.funding_fee_rub == Decimal("36.00")
    assert result.unexpected_reserve_rub == Decimal("24.00")
    assert result.seller_fee_rub == Decimal("204.00")
    assert result.withdrawal_fee_rub == Decimal("44.88")
    assert result.total_invested_rub == Decimal("1280.00")
    assert result.net_profit_rub == Decimal("171.12")
    assert result.roi_pct == Decimal("13.37")


def test_platform_fees_fixed_minimum_cashout(quotes, settings):
    settings = settings.model_copy(
        update={
            "mode": "live",
            "venue_fees": {
                "csfloat": VenueFees(verified=True, funding_pct=2, funding_fixed_rub=10),
                "skinport": VenueFees(
                    verified=True,
                    cashout_available=True,
                    seller_pct=8,
                    withdrawal_pct=2,
                    withdrawal_fixed_rub=30,
                    withdrawal_minimum_rub=2000,
                ),
            },
        }
    )
    result = calculate_costs(*quotes, settings)
    assert result.funding_fee_rub == Decimal("34.00")
    assert result.withdrawal_fee_rub == Decimal("61.28")
    assert result.net_profit_rub == Decimal("244.72")
    assert not result.cashout_eligible


def test_missing_live_fees_rejected(quotes, settings):
    with pytest.raises(ValueError, match="fee profiles"):
        calculate_costs(*quotes, settings.model_copy(update={"mode": "live"}))


def test_currency_conversion(settings):
    assert Decimal("10.1") * settings.rate("USD") == Decimal("909.0")
    assert settings.rate("RUB") == 1
    assert settings.rate("EUR") == 98
    with pytest.raises(ValueError):
        settings.rate("XYZ")


@pytest.mark.parametrize(
    "other",
    [
        "AK-47 | Redline (Minimal Wear)",
        "StatTrak™ AK-47 | Redline (Field-Tested)",
        "Souvenir AK-47 | Redline (Field-Tested)",
    ],
)
def test_distinct_variants_not_matched(quotes, other):
    assert not can_compare(quotes[0].item, identity_from_market_hash_name(other))[0]


@pytest.mark.parametrize(
    "field,left,right",
    [
        ("float_value", Decimal("0.15"), Decimal("0.37")),
        ("paint_seed", 1, 2),
        ("stickers", ("rare",), ("ordinary",)),
    ],
)
def test_premium_mismatch_rejected(quotes, field, left, right):
    assert not can_compare(
        quotes[0].item.model_copy(update={field: left}),
        quotes[1].item.model_copy(update={field: right}),
    )[0]


def test_missing_premium_attribute_warns(quotes):
    matched, warnings = can_compare(
        quotes[0].item.model_copy(update={"paint_seed": 1}), quotes[1].item
    )
    assert matched and "pattern" in warnings[0]


def test_ask_spread_is_limited_not_executable(quotes, settings):
    result = OpportunityScanner(settings).scan(quotes)
    assert len(result) == 1
    assert not result[0].executable
    assert result[0].confidence == Confidence.LIMITED
    assert any("ask" in warning for warning in result[0].warnings)


def test_history_prices_never_used_as_sell_bid(quotes, settings):
    quotes[1].kind = QuoteKind.MEDIAN_SALE
    assert not OpportunityScanner(settings).scan(quotes)


def test_executable_bid_type_preserved(quotes, settings):
    quotes[1].kind = QuoteKind.BUY_ORDER
    result = OpportunityScanner(settings).scan(quotes)
    assert result[0].executable


@pytest.mark.parametrize(
    "updates",
    [
        {"budget_rub": Decimal("1300"), "reserve_rub": Decimal("100")},
        {"min_profit_rub": Decimal("300")},
        {"max_item_price_rub": Decimal("1100")},
        {"min_roi_pct": Decimal("20")},
        {"min_liquidity_score": 100},
        {"max_hold_hours": 1},
        {"enabled_providers": ("csfloat",)},
        {"excluded_items": ("AK-47 | Redline (Field-Tested)",)},
    ],
)
def test_filters(quotes, settings, updates):
    assert not OpportunityScanner(settings.model_copy(update=updates)).scan(quotes)


def test_stale_quote_rejected(quotes, settings):
    quotes[0].observed_at = datetime.now(UTC) - timedelta(hours=1)
    assert not OpportunityScanner(settings).scan(quotes)


def test_unknown_liquidity_separated(quotes, settings):
    quotes[1].sales_7d = quotes[1].volume_24h = None
    quotes[1].listing_count = 900
    result = OpportunityScanner(settings).scan(quotes)
    assert result[0].liquidity == Liquidity.UNKNOWN
    assert result[0].liquidity_score == 0
    assert result[0].confidence == Confidence.INSUFFICIENT
    assert not OpportunityScanner(
        settings.model_copy(update={"allow_unknown_liquidity": False})
    ).scan(quotes)


def test_unknown_hold_requires_explicit_filter(quotes, settings):
    quotes[0].transfer_delay_hours = None
    assert not OpportunityScanner(settings).scan(quotes)
    result = OpportunityScanner(settings.model_copy(update={"allow_unknown_hold": True})).scan(
        quotes
    )
    assert result[0].hold_hours is None


def test_live_requires_fresh_fx(quotes, settings):
    fees = VenueFees(verified=True, cashout_available=True, seller_pct=12)
    settings = settings.model_copy(
        update={"mode": "live", "venue_fees": {"csfloat": fees, "skinport": fees}}
    )
    assert not OpportunityScanner(settings).scan(quotes)
    settings = settings.model_copy(update={"fx_verified_at": datetime.now(UTC)})
    assert OpportunityScanner(settings).scan(quotes)
    settings = settings.model_copy(update={"fx_verified_at": datetime.now(UTC) - timedelta(days=2)})
    assert not OpportunityScanner(settings).scan(quotes)


@pytest.mark.parametrize(
    "updates",
    [
        {"budget_rub": 999},
        {"budget_rub": 5001},
        {"reserve_rub": 5000},
        {"host": "0.0.0.0"},
        {"usd_rub": "NaN"},
        {"enabled_providers": "steam"},
    ],
)
def test_invalid_settings(updates):
    with pytest.raises(ValidationError):
        Settings(_env_file=None, **updates)
