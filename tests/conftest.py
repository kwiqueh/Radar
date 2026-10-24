from datetime import UTC, datetime
from decimal import Decimal

import pytest

from skin_radar.config import Settings
from skin_radar.core.matching import identity_from_market_hash_name
from skin_radar.models import MarketQuote, QuoteKind


@pytest.fixture
def settings(tmp_path):
    return Settings(_env_file=None, database_path=tmp_path / "radar.db")


@pytest.fixture
def quotes():
    identity = identity_from_market_hash_name("AK-47 | Redline (Field-Tested)")
    common = dict(
        item=identity, currency="USD", observed_at=datetime.now(UTC), transfer_delay_hours=24
    )
    return [
        MarketQuote(
            **common,
            provider="csfloat",
            quote_id="listing-1",
            price=Decimal("13"),
            price_rub=Decimal("1200"),
            url="https://csfloat.com/item/listing-1",
            kind=QuoteKind.ACTIVE_LISTING,
        ),
        MarketQuote(
            **common,
            provider="skinport",
            quote_id="sku-1",
            price=Decimal("19"),
            price_rub=Decimal("1700"),
            url="https://skinport.com/market/730",
            kind=QuoteKind.ACTIVE_LISTING,
            sales_7d=28,
            volume_24h=7,
        ),
    ]
