from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from skin_radar.config import Settings
from skin_radar.core.matching import identity_from_market_hash_name
from skin_radar.models import MarketQuote, QuoteKind
from skin_radar.providers.base import MarketProvider, ProviderResult
from skin_radar.providers.http import ResilientHttpClient


class SkinportProvider(MarketProvider):
    name = "skinport"
    endpoint = "https://api.skinport.com/v1/items"

    def __init__(self, settings: Settings, client: ResilientHttpClient):
        self.settings = settings
        self.client = client

    async def fetch(self) -> ProviderResult:
        payload = await self.client.get_json(
            self.endpoint,
            params={"app_id": 730, "currency": "USD", "tradable": 1},
            headers={"Accept-Encoding": "br, gzip"},
        )
        if not isinstance(payload, list):
            raise ValueError("Skinport returned an unexpected response type")
        quotes = [parsed for row in payload if (parsed := self._parse(row)) is not None]
        return ProviderResult(quotes, f"{len(quotes)} public aggregate lowest asks loaded")

    def _parse(self, row: Any) -> MarketQuote | None:
        try:
            if not isinstance(row, dict):
                return None
            name = str(row["market_hash_name"])
            price_usd = Decimal(str(row["min_price"]))
            quantity = int(row.get("quantity", 0))
            if price_usd <= 0 or quantity <= 0:
                return None
            page = row.get("market_page") or (
                "https://skinport.com/market/730?search=" + quote(name, safe="")
            )
            return MarketQuote(
                provider=self.name,
                quote_id=f"{name}:lowest-ask",
                item=identity_from_market_hash_name(name),
                kind=QuoteKind.ACTIVE_LISTING,
                price=price_usd,
                currency="USD",
                price_rub=price_usd * self.settings.usd_rub,
                url=str(page),
                observed_at=datetime.now(UTC),
                listing_count=quantity,
                aggregate=True,
                raw_label="public aggregate min_price (lowest ask)",
            )
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return None
