from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from skin_radar.core.matching import identity_from_market_hash_name
from skin_radar.models import MarketQuote, QuoteKind
from skin_radar.providers.base import MarketProvider, ProviderResult
from skin_radar.providers.http import ResilientHttpClient


class MarketCSGOProvider(MarketProvider):
    """Public RUB catalog. Semantics of price/volume have not been independently verified.

    Never turn this reference into an active listing, a bid, or executed sales.
    Source timestamp is preserved even when it is stale.
    """

    name = "market_csgo"
    endpoint = "https://market.csgo.com/api/v2/prices/RUB.json"

    def __init__(self, client: ResilientHttpClient):
        self.client = client

    async def fetch(self) -> ProviderResult:
        payload = await self.client.get_json(self.endpoint)
        if (
            not isinstance(payload, dict)
            or payload.get("success") is not True
            or payload.get("currency") != "RUB"
            or not isinstance(payload.get("items"), list)
        ):
            raise ValueError("Market.CSGO catalog schema changed")
        timestamp = payload.get("time")
        if not isinstance(timestamp, (int, float)) or isinstance(timestamp, bool):
            raise ValueError("Market.CSGO source timestamp is missing")
        observed = datetime.fromtimestamp(timestamp, UTC)
        quotes = []
        for row in payload["items"]:
            try:
                name = row["market_hash_name"]
                price = Decimal(str(row["price"]))
                if not isinstance(name, str) or not price.is_finite() or price <= 0:
                    continue
                quotes.append(
                    MarketQuote(
                        provider=self.name,
                        quote_id=f"{name}:catalog-rub",
                        item=identity_from_market_hash_name(name),
                        kind=QuoteKind.CATALOG_REFERENCE,
                        price=price,
                        price_rub=price,
                        currency="RUB",
                        observed_at=observed,
                        aggregate=True,
                        url="https://market.csgo.com/en/",
                        raw_label="Справочный каталог: тип цены и volume не подтверждены",
                    )
                )
            except (KeyError, TypeError, ValueError, InvalidOperation):
                continue
        if payload["items"] and not quotes:
            raise ValueError("Market.CSGO returned no valid catalog rows")
        return ProviderResult(quotes, f"{len(quotes)} справочных цен RUB; не bids и не сделки")
