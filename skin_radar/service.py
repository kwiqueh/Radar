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
from skin_radar.local_settings import SetupUpdate, save_local_settings
from skin_radar.models import Opportunity, ProviderState, QuoteKind
from skin_radar.providers import CSFloatProvider, DemoProvider, SkinportProvider
from skin_radar.providers.base import MarketProvider
from skin_radar.providers.http import APIError, ResilientHttpClient
from skin_radar.providers.market_csgo import MarketCSGOProvider
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
        self._provider_cache = {}
        if settings.mode == "live" and not settings.venue_fees and settings.fees_file.is_file():
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
        if "market_csgo" in enabled:
            providers.append(MarketCSGOProvider(self.http))
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
                    replace_quotes=False,
                )
                if self.settings.mode == "live" and self.notifier.configured:
                    await self._send_new_alerts(items[:5])
                return items
            results = await asyncio.gather(
                *(self._fetch_provider(provider) for provider in providers), return_exceptions=True
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
                            detail=self._provider_error(provider.name, result),
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
        if not self.settings.enabled_providers:
            return ["В блоке «Настройка реальных данных» выберите хотя бы одну площадку."]
        if not self.settings.api_terms_accepted:
            return [
                "Чтение API выключено. В блоке «Настройка реальных данных» подтвердите "
                "право использования выбранных API и нажмите «Сохранить и проверить источники». "
                "Для просмотра рублёвого каталога Market.CSGO курсы и комиссии не нужны."
            ]
        trading_quotes = [
            q
            for q in self._cached_quotes
            if not q.demo
            and q.provider in self.settings.enabled_providers
            and q.kind in {QuoteKind.ACTIVE_LISTING, QuoteKind.BUY_ORDER}
        ]
        sources = {q.provider for q in trading_quotes}
        if len(sources) < 2:
            message = "Для расчёта межплощадочной маржи нужны котировки минимум двух площадок. "
            message += "Причины отказов подключения указаны в разделе «Состояние API»."
            if "market_csgo" in self.settings.enabled_providers:
                return [
                    "RUB-каталог Market.CSGO можно смотреть без настройки курсов и комиссий. "
                    "Его справочные цены не участвуют в расчёте арбитража.",
                    message,
                ]
            return [message]
        warnings = []
        verified = self.settings.fx_verified_at
        needs_fx = any(q.currency != "RUB" for q in trading_quotes)
        if needs_fx and (
            verified is None
            or verified.tzinfo is None
            or not 0
            <= (datetime.now(UTC) - verified).total_seconds()
            <= self.settings.max_fx_age_hours * 3600
        ):
            warnings.append(
                "Для расчёта маржи укажите фактические курсы в настройках "
                "и отметьте «Я проверил указанные курсы сейчас»."
            )
        for name in sorted(sources):
            fees = self.settings.venue_fees.get(name)
            if fees is None or not fees.verified:
                warnings.append(
                    f"{name}: для расчёта маржи заполните тарифы "
                    "в блоке «Комиссии для расчёта прибыли» и подтвердите их проверку."
                )
            elif not fees.cashout_available:
                warnings.append(f"{name}: вывод средств недоступен в профиле.")
        if not self.settings.allow_unknown_hold:
            warnings.append("Предметы с неизвестным сроком передачи исключаются.")
        if "market_csgo" in self.settings.enabled_providers:
            warnings.append(
                "Market.CSGO: справочный каталог виден; эти цены не участвуют в арбитраже."
            )
        return warnings

    def current_opportunities(self, limit: int = 100) -> list[Opportunity]:
        quotes = self._cached_quotes
        if not quotes:
            saved = self.storage.opportunities(limit)
            quotes = [q for item in saved for q in (item.buy, item.sell)]
        return OpportunityScanner(self.settings).scan(quotes)[:limit]

    @staticmethod
    def _provider_error(name: str, error: BaseException) -> str:
        if not isinstance(error, APIError):
            return f"{type(error).__name__}: данные API не распознаны; demo не подставлено."
        detail = str(error)
        if "HTTP 403" in detail or "HTTP 401" in detail:
            if name == "csfloat":
                return "Доступ запрещён (401/403). Укажите свой API-ключ CSFloat; cookies не нужны."
            return "Доступ запрещён (401/403): ограничение площадки/сети. Защита не обходится."
        return detail  # APIError is deliberately sanitized by ResilientHttpClient.

    def setup_status(self) -> dict:
        s = self.settings
        return {
            "mode": s.mode,
            "enabled_providers": s.enabled_providers,
            "api_terms_accepted": s.api_terms_accepted,
            "usd_rub": str(s.usd_rub),
            "eur_rub": str(s.eur_rub),
            "fx_verified_at": s.fx_verified_at.isoformat() if s.fx_verified_at else None,
            "csfloat_key_set": bool(s.csfloat_api_key),
            "venue_fees": {
                name: fees.model_dump(mode="json") for name, fees in s.venue_fees.items()
            },
            "warnings": self.configuration_warnings(),
        }

    async def apply_setup(self, update: SetupUpdate) -> dict:
        async with self._scan_lock:
            values = update.model_dump(exclude_none=True)
            settings = Settings.model_validate({**self.settings.model_dump(), **values})
            # Write successfully before changing the running service. Mode histories stay separate.
            save_local_settings(settings.local_config_file, update)
            changed_mode = settings.mode != self.settings.mode
            old_key = self.settings.csfloat_api_key
            self.settings = settings
            if changed_mode:
                self.storage = SQLiteStorage(
                    settings.database_path.with_stem(
                        f"{settings.database_path.stem}.{settings.mode}"
                    )
                )
                self._apply_saved_filters()
            # Explicitly selected sources take precedence over old filter selections.
            if update.enabled_providers is not None:
                self.settings = Settings.model_validate(
                    {**self.settings.model_dump(), "enabled_providers": update.enabled_providers}
                )
                self.storage.set_filter("enabled_providers", ",".join(update.enabled_providers))
            self._cached_quotes, self._cached_states = [], []
            # Keep per-process provider cooldown across mode and settings changes.
            self._last_fetch = 0.0
            cached = self._provider_cache.get("csfloat")
            if old_key != settings.csfloat_api_key and cached and isinstance(cached[1], APIError):
                if any(code in str(cached[1]) for code in ("HTTP 401", "HTTP 403")):
                    self._provider_cache.pop("csfloat")
            return self.setup_status()

    async def _fetch_provider(self, provider):
        cached = self._provider_cache.get(provider.name)
        if cached and isinstance(cached[1], APIError):
            if any(code in str(cached[1]) for code in ("HTTP 401", "HTTP 403")):
                # Authentication/anti-bot denials need a changed prerequisite, not more polling.
                raise cached[1]
        if cached and time.monotonic() - cached[0] < max(370, self.settings.scan_interval_seconds):
            result = cached[1]
            if isinstance(result, Exception):
                raise result
            from skin_radar.providers.base import ProviderResult

            return ProviderResult(
                [
                    q
                    if q.demo
                    else q.model_copy(
                        update={"price_rub": q.price * self.settings.rate(q.currency)}
                    )
                    for q in result.quotes
                ],
                result.detail,
            )
        try:
            result = await provider.fetch()
        except Exception as exc:
            self._provider_cache[provider.name] = (time.monotonic(), exc)
            raise
        self._provider_cache[provider.name] = (time.monotonic(), result)
        return result
