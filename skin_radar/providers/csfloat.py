from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any
from urllib.parse import quote

from skin_radar.config import Settings
from skin_radar.core.matching import identity_from_market_hash_name
from skin_radar.models import MarketQuote, QuoteKind
from skin_radar.providers.base import MarketProvider, ProviderResult
from skin_radar.providers.http import ResilientHttpClient


def _datetime(value: Any) -> datetime:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(UTC)


class CSFloatProvider(MarketProvider):
    name = "csfloat"
    endpoint = "https://csfloat.com/api/v1/listings"

    def __init__(self, settings: Settings, client: ResilientHttpClient):
        self.settings = settings
        self.client = client

    async def fetch(self) -> ProviderResult:
        cursor: str | None = None
        quotes: list[MarketQuote] = []
        seen: set[str] = set()
        for page in range(self.settings.csfloat_pages):
            params: dict[str, str | int] = {
                "limit": 50,
                "sort_by": "lowest_price",
                "type": "buy_now",
            }
            if cursor:
                params["cursor"] = cursor
            headers = {}
            if self.settings.csfloat_api_key:
                headers["Authorization"] = self.settings.csfloat_api_key
            payload = await self.client.get_json(self.endpoint, params=params, headers=headers)
            if isinstance(payload, list):
                rows, next_cursor = payload, None
            elif isinstance(payload, dict):
                rows = payload.get("data", payload.get("listings"))
                next_cursor = payload.get("cursor")
            else:
                raise ValueError("CSFloat returned an unexpected response type")
            if not isinstance(rows, list):
                raise ValueError("CSFloat listings field is not a list")
            quotes.extend(parsed for row in rows if (parsed := self._parse(row)) is not None)
            if not next_cursor or next_cursor in seen or not rows:
                break
            seen.add(next_cursor)
            cursor = next_cursor
            if page + 1 < self.settings.csfloat_pages:
                await asyncio.sleep(1.5)
        return ProviderResult(quotes, f"{len(quotes)} public active listings loaded")

    def _parse(self, row: Any) -> MarketQuote | None:
        try:
            if not isinstance(row, dict):
                return None
            if row.get("type", "buy_now") != "buy_now" or row.get("state", "listed") != "listed":
                return None
            item = row["item"]
            name = str(item["market_hash_name"])
            price_usd = Decimal(str(row["price"])) / 100
            if price_usd <= 0:
                return None
            stickers = tuple(
                str(sticker.get("name", sticker.get("sticker_id", "unknown")))
                for sticker in item.get("stickers", [])
                if isinstance(sticker, dict)
            )
            identity = identity_from_market_hash_name(
                name,
                float_value=Decimal(str(item["float_value"]))
                if item.get("float_value") is not None
                else None,
                paint_seed=int(item["paint_seed"]) if item.get("paint_seed") is not None else None,
                stickers=stickers,
            )
            listing_id = str(row["id"])
            return MarketQuote(
                provider=self.name,
                quote_id=listing_id,
                item=identity,
                kind=QuoteKind.ACTIVE_LISTING,
                price=price_usd,
                currency="USD",
                price_rub=price_usd * self.settings.usd_rub,
                url=f"https://csfloat.com/item/{quote(listing_id, safe='')}",
                observed_at=datetime.now(UTC),
                listing_created_at=_datetime(row["created_at"]) if row.get("created_at") else None,
                transfer_delay_hours=None,
                raw_label="public active listing (ask)",
            )
        except (KeyError, TypeError, ValueError, InvalidOperation):
            return None
