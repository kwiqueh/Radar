from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import BaseModel, Field, computed_field, field_validator


class QuoteKind(StrEnum):
    ACTIVE_LISTING = "active_listing"
    BUY_ORDER = "buy_order"
    LAST_SALE = "last_sale"
    MEDIAN_SALE = "median_sale"


class Confidence(StrEnum):
    CONFIRMED = "confirmed"
    LIMITED = "limited"
    INSUFFICIENT = "insufficient_data"


class Liquidity(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"
    UNKNOWN = "unknown"


class ItemIdentity(BaseModel):
    market_hash_name: str
    exterior: str | None = None
    stattrak: bool = False
    souvenir: bool = False
    float_value: Decimal | None = None
    paint_seed: int | None = None
    stickers: tuple[str, ...] = ()
    tradable_after: datetime | None = None

    @field_validator("market_hash_name")
    @classmethod
    def nonempty_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("market_hash_name cannot be empty")
        return value


class MarketQuote(BaseModel):
    provider: str
    quote_id: str
    item: ItemIdentity
    kind: QuoteKind
    price: Decimal = Field(gt=0)
    currency: str
    price_rub: Decimal = Field(gt=0)
    url: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    volume_24h: int | None = Field(default=None, ge=0)
    sales_7d: int | None = Field(default=None, ge=0)
    seller_count: int | None = Field(default=None, ge=0)
    listing_count: int | None = Field(default=None, ge=0)
    spread_pct: Decimal | None = None
    volatility_pct: Decimal | None = None
    transfer_delay_hours: int | None = Field(default=None, ge=0)
    listing_created_at: datetime | None = None
    aggregate: bool = False
    demo: bool = False
    raw_label: str = ""

    @field_validator("url")
    @classmethod
    def safe_https_url(cls, value: str) -> str:
        parsed = urlsplit(value)
        if parsed.scheme != "https" or parsed.hostname not in {
            "csfloat.com",
            "skinport.com",
            "market.csgo.com",
        }:
            raise ValueError("Quote links must use HTTPS on an integrated marketplace")
        if parsed.username or parsed.password:
            raise ValueError("Credentials in quote links are forbidden")
        return value


class CostBreakdown(BaseModel):
    purchase_price_rub: Decimal
    purchase_fee_rub: Decimal
    funding_fee_rub: Decimal
    unexpected_reserve_rub: Decimal
    total_invested_rub: Decimal
    sale_price_rub: Decimal
    seller_fee_rub: Decimal
    withdrawal_fee_rub: Decimal
    net_proceeds_rub: Decimal
    fixed_cost_rub: Decimal
    net_profit_rub: Decimal
    roi_pct: Decimal
    withdrawal_minimum_rub: Decimal = Decimal("0")
    cashout_eligible: bool = True


class Opportunity(BaseModel):
    fingerprint: str
    item_name: str
    buy: MarketQuote
    sell: MarketQuote
    costs: CostBreakdown
    liquidity: Liquidity
    liquidity_score: int = Field(ge=0, le=100)
    risk_score: int = Field(ge=0, le=100)
    confidence: Confidence
    hold_hours: int | None = Field(default=None, ge=0)
    warnings: tuple[str, ...] = ()
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @computed_field
    @property
    def executable(self) -> bool:
        return self.sell.kind == QuoteKind.BUY_ORDER


class ProviderState(BaseModel):
    name: str
    enabled: bool
    healthy: bool
    detail: str
    quote_count: int = 0
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
