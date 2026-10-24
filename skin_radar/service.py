from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from skin_radar.alerts.telegram import TelegramNotifier
from skin_radar.config import Settings, VenueFees
from skin_radar.core.scanner import OpportunityScanner
from skin_radar.models import Opportunity, ProviderState
from skin_radar.providers import CSFloatProvider, DemoProvider, SkinportProvider
from skin_radar.providers.base import MarketProvider
from skin_radar.providers.http import ResilientHttpClient
from skin_radar.storage import SQLiteStorage

LOG = logging.getLogger(__name__)


def parse_bool(value: str) -> bool:
    if value.lower() not in {"true", "false"}:
        raise ValueError("Use true or false for boolean filters")
    return value.lower() == "true"


FILTER_FIELDS = {
    "budget_rub": Decimal,
    "reserve_rub": Decimal,
    "max_item_price_rub": Decimal,
    "min_profit_rub": Decimal,
    "min_roi_pct": Decimal,
    "min_liquidity_score": int,
    "max_quote_age_seconds": int,
    "max_hold_hours": int,
    "allow_unknown_hold": parse_bool,
    "allow_unknown_liquidity": parse_bool,
    "excluded_items": lambda value: tuple(x.strip() for x in value.split(",") if x.strip()),
    "enabled_providers": lambda value: tuple(x.strip() for x in value.split(",") if x.strip()),
}


class RadarService:
    def __init__(self, settings: Settings):
        self.settings = settings
        # Keep synthetic history and alert claims physically separate from live data.
        database_path = settings.database_path.with_stem(
            f"{settings.database_path.stem}.{settings.mode}"
        )
        self.storage = SQLiteStorage(database_path)
        self.http = ResilientHttpClient(settings.http_concurrency)
        self.notifier = TelegramNotifier(
            settings.telegram_bot_token, settings.telegram_chat_id, self.http
        )
        self._scan_lock = asyncio.Lock()
        self._last_fetch = 0.0
        self._cached_quotes = []
        self._cached_states = []
        if settings.mode == "live" and settings.fees_file.is_file():
            data = json.loads(settings.fees_file.read_text(encoding="utf-8"))
            self.settings = Settings.model_validate(
                {
                    **settings.model_dump(),
                    "venue_fees": {
                        name: VenueFees.model_validate(fees) for name, fees in data.items()
                    },
                }
            )
        self._apply_saved_filters()

    def _apply_saved_filters(self) -> None:
        updates: dict[str, object] = {}
        for name, value in self.storage.filters().items():
            if name in FILTER_FIELDS:
                try:
                    updates[name] = FILTER_FIELDS[name](value)
                except (ValueError, InvalidOperation):
                    LOG.warning("Ignoring invalid saved filter %s", name)
        if updates:
            self.settings = Settings.model_validate({**self.settings.model_dump(), **updates})

    def providers(self) -> list[MarketProvider]:
        if self.settings.mode == "demo":
            return [DemoProvider()]
        providers: list[MarketProvider] = []
        enabled = set(self.settings.enabled_providers)
        if "csfloat" in enabled:
            providers.append(CSFloatProvider(self.settings, self.http))
        if "skinport" in enabled:
            providers.append(SkinportProvider(self.settings, self.http))
        return providers

    async def scan(self) -> list[Opportunity]:
        async with self._scan_lock:
            started = datetime.now(UTC)
            providers = (
                self.providers()
                if self.settings.mode == "demo" or self.settings.api_terms_accepted
                else []
            )
            use_cache = self._last_fetch and time.monotonic() - self._last_fetch < max(
                370, self.settings.scan_interval_seconds
            )
            if use_cache:
                items = OpportunityScanner(self.settings).scan(self._cached_quotes)
                self.storage.save_scan(
                    quotes=[],
                    opportunities=items,
                    states=self._cached_states,
                    started_at=started,
                    mode=self.settings.mode,
                )
                if self.settings.mode == "live" and self.notifier.configured:
                    await self._send_new_alerts(items[:5])
                return items
            results = await asyncio.gather(
                *(provider.fetch() for provider in providers), return_exceptions=True
            )
            quotes = []
            states: list[ProviderState] = []
            for provider, result in zip(providers, results, strict=True):
                if isinstance(result, BaseException):
                    LOG.error("Provider %s failed (%s)", provider.name, type(result).__name__)
                    states.append(
                        ProviderState(
                            name=provider.name,
                            enabled=True,
                            healthy=False,
                            detail=f"{type(result).__name__}: API недоступен, demo не подставлено.",
                        )
                    )
                else:
                    quotes.extend(result.quotes)
                    states.append(
                        ProviderState(
                            name=provider.name,
                            enabled=True,
                            healthy=True,
                            detail=result.detail,
                            quote_count=len(result.quotes),
                        )
                    )
            states.extend(
                [
                    ProviderState(
                        name="market_csgo",
                        enabled=False,
                        healthy=False,
                        detail="Отключён: документация недоступна из среды; адаптер не проверен.",
                    ),
                    ProviderState(
                        name="steam_community_market",
                        enabled=False,
                        healthy=False,
                        detail="Отключён: API цен и разрешение сбора не подтверждены.",
                    ),
                ]
            )
            if self.settings.mode == "live" and not self.settings.api_terms_accepted:
                states.extend(
                    ProviderState(
                        name=name,
                        enabled=False,
                        healthy=False,
                        detail="Выключено: проверьте условия API; API_TERMS_ACCEPTED=true.",
                    )
                    for name in self.settings.enabled_providers
                )
            scanner = OpportunityScanner(self.settings)
            opportunities = scanner.scan(quotes)
            self._cached_quotes = quotes
            self._cached_states = states
            self._last_fetch = time.monotonic()
            self.storage.save_scan(
                quotes=quotes,
                opportunities=opportunities,
                states=states,
                started_at=started,
                mode=self.settings.mode,
            )
            if self.settings.mode == "live" and self.notifier.configured:
                await self._send_new_alerts(opportunities[:5])
            return opportunities

    async def _send_new_alerts(self, opportunities: list[Opportunity]) -> None:
        for item in opportunities:
            if not self.storage.claim_alert(item, "telegram"):
                continue
            try:
                await self.notifier.send(item)
                self.storage.set_alert_status(item.fingerprint, "sent")
            except Exception:
                # A timeout can follow a successful send; keep the claim to avoid duplicates.
                LOG.error("Telegram delivery uncertain; alert reservation retained")
                self.storage.set_alert_status(item.fingerprint, "uncertain")

    def update_filter(self, name: str, raw_value: str) -> Settings:
        return self.update_filters({name: raw_value})

    def update_filters(self, values: dict[str, str]) -> Settings:
        updates = {}
        for name, value in values.items():
            if name not in FILTER_FIELDS:
                raise ValueError(f"Unknown filter: {name}")
            try:
                updates[name] = FILTER_FIELDS[name](value)
            except (ValueError, InvalidOperation) as exc:
                raise ValueError(f"Invalid filter: {name}") from exc
        updated = Settings.model_validate({**self.settings.model_dump(), **updates})
        self.storage.set_filters(values)
        self.settings = updated
        return updated

    async def close(self) -> None:
        await self.http.close()

    def configuration_warnings(self) -> list[str]:
        if self.settings.mode == "demo":
            return ["DEMO: синтетические котировки; автоматические уведомления отключены."]
        warnings = []
        if not self.settings.api_terms_accepted:
            warnings.append("Условия API не подтверждены; запросы выключены.")
        verified = self.settings.fx_verified_at
        if (
            verified is None
            or verified.tzinfo is None
            or not 0
            <= (datetime.now(UTC) - verified).total_seconds()
            <= self.settings.max_fx_age_hours * 3600
        ):
            warnings.append("Укажите фактические курсы и свежий FX_VERIFIED_AT с часовым поясом.")
        for name in self.settings.enabled_providers:
            fees = self.settings.venue_fees.get(name)
            if fees is None or not fees.verified:
                warnings.append(f"{name}: профиль комиссий не подтверждён в fees.local.json.")
            elif not fees.cashout_available:
                warnings.append(f"{name}: вывод средств недоступен в профиле.")
        if not self.settings.allow_unknown_hold:
            warnings.append("Предметы с неизвестным сроком передачи исключаются.")
        return warnings
