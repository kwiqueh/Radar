from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class VenueFees(BaseModel):
    purchase_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    funding_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    seller_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    withdrawal_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    funding_fixed_rub: Decimal = Field(default=Decimal("0"), ge=0)
    withdrawal_fixed_rub: Decimal = Field(default=Decimal("0"), ge=0)
    withdrawal_minimum_rub: Decimal = Field(default=Decimal("0"), ge=0)
    cashout_available: bool = False
    verified: bool = False
    note: str = "Тарифы и региональная доступность вывода не подтверждены"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SKIN_RADAR_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        env_ignore_empty=True,
        allow_inf_nan=False,
    )

    mode: str = "demo"
    database_path: Path = Path("skin_radar.db")
    local_config_file: Path = Path("settings.local.json")
    host: str = "127.0.0.1"
    port: int = Field(default=8000, ge=1, le=65535)
    scan_interval_seconds: int = Field(default=370, ge=60)

    budget_rub: Decimal = Field(default=Decimal("5000"), ge=1000, le=5000)
    reserve_rub: Decimal = Field(default=Decimal("500"), ge=0)
    max_item_price_rub: Decimal = Field(default=Decimal("4000"), gt=0)
    min_profit_rub: Decimal = Field(default=Decimal("150"), ge=0)
    min_roi_pct: Decimal = Field(default=Decimal("8"), ge=0)
    min_liquidity_score: int = Field(default=20, ge=0, le=100)
    max_quote_age_seconds: int = Field(default=900, ge=30)
    max_hold_hours: int = Field(default=168, ge=0)
    usd_rub: Decimal = Field(default=Decimal("90"), gt=0)
    eur_rub: Decimal = Field(default=Decimal("98"), gt=0)
    fx_verified_at: datetime | None = None
    max_fx_age_hours: int = Field(default=24, ge=1)
    fees_file: Path = Path("fees.local.json")
    venue_fees: dict[str, VenueFees] = Field(default_factory=dict)
    allow_unknown_liquidity: bool = True
    allow_unknown_hold: bool = False
    api_terms_accepted: bool = False
    csfloat_api_key: str = Field(default="", repr=False)

    purchase_fee_pct: Decimal = Field(default=Decimal("0"), ge=0, le=100)
    funding_fee_pct: Decimal = Field(default=Decimal("3"), ge=0, le=100)
    seller_fee_pct: Decimal = Field(default=Decimal("12"), ge=0, le=100)
    withdrawal_fee_pct: Decimal = Field(default=Decimal("3"), ge=0, le=100)
    fixed_cost_rub: Decimal = Field(default=Decimal("0"), ge=0)
    unexpected_cost_reserve_pct: Decimal = Field(default=Decimal("2"), ge=0, le=100)

    enabled_providers: Annotated[tuple[str, ...], NoDecode] = ("csfloat", "skinport", "market_csgo")
    excluded_items: Annotated[tuple[str, ...], NoDecode] = ()
    csfloat_pages: int = Field(default=2, ge=1, le=10)
    http_concurrency: int = Field(default=4, ge=1, le=16)
    telegram_bot_token: str = Field(default="", repr=False)
    telegram_chat_id: str = ""

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, value: str) -> str:
        value = value.lower().strip()
        if value not in {"demo", "live"}:
            raise ValueError("mode must be demo or live")
        return value

    @field_validator("enabled_providers", "excluded_items", mode="before")
    @classmethod
    def split_csv(cls, value: object) -> object:
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value

    @model_validator(mode="after")
    def validate_budget(self) -> Settings:
        if self.reserve_rub >= self.budget_rub:
            raise ValueError("reserve_rub must be smaller than budget_rub")
        if self.max_item_price_rub > self.budget_rub - self.reserve_rub:
            raise ValueError("max_item_price_rub must fit inside budget minus reserve")
        if not set(self.enabled_providers) <= {"csfloat", "skinport", "market_csgo"}:
            raise ValueError("Unknown provider")
        if self.host not in {"127.0.0.1", "::1", "localhost"}:
            raise ValueError("Dashboard is local-only; host must be a loopback address")
        return self

    def rate(self, currency: str) -> Decimal:
        rates = {"RUB": Decimal("1"), "USD": self.usd_rub, "EUR": self.eur_rub}
        try:
            return rates[currency.upper()]
        except KeyError as exc:
            raise ValueError(f"Unsupported currency: {currency}") from exc
