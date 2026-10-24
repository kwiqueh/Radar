from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from skin_radar.core.matching import identity_from_market_hash_name
from skin_radar.models import MarketQuote, QuoteKind
from skin_radar.providers.base import MarketProvider, ProviderResult


class DemoProvider(MarketProvider):
    """Synthetic quotes for tests and screenshots; never represented as live market data."""

    name = "demo"

    async def fetch(self) -> ProviderResult:
        now = datetime.now(UTC)
        name = "AK-47 | Redline (Field-Tested)"
        item = identity_from_market_hash_name(name)
        quotes = [
            MarketQuote(
                provider="csfloat",
                quote_id="DEMO-CF-001",
                item=item,
                kind=QuoteKind.ACTIVE_LISTING,
                price=Decimal("13.33"),
                currency="USD",
                price_rub=Decimal("1200"),
                url="https://csfloat.com/",
                observed_at=now,
                volume_24h=7,
                sales_7d=28,
                seller_count=12,
                spread_pct=Decimal("6.4"),
                transfer_delay_hours=24,
                raw_label="DEMO: synthetic active listing",
                demo=True,
            ),
            MarketQuote(
                provider="skinport",
                quote_id="DEMO-SP-AK47-REDLINE-FT",
                item=item,
                kind=QuoteKind.ACTIVE_LISTING,
                price=Decimal("18.89"),
                currency="USD",
                price_rub=Decimal("1700"),
                url="https://skinport.com/market/730",
                observed_at=now,
                volume_24h=7,
                sales_7d=28,
                seller_count=18,
                spread_pct=Decimal("6.4"),
                raw_label="DEMO: synthetic lowest ask",
                demo=True,
                transfer_delay_hours=24,
            ),
            MarketQuote(
                provider="skinport",
                quote_id="DEMO-SP-M4A1-S",
                item=identity_from_market_hash_name("M4A1-S | Decimator (Field-Tested)"),
                kind=QuoteKind.ACTIVE_LISTING,
                price=Decimal("12.50"),
                currency="USD",
                price_rub=Decimal("1125"),
                url="https://skinport.com/market/730",
                observed_at=now,
                seller_count=5,
                raw_label="DEMO: unmatched quote",
                demo=True,
            ),
        ]
        return ProviderResult(quotes, "DEMO: 3 synthetic quotes; not real deals")
