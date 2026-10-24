from __future__ import annotations

import hashlib
import math
from collections import defaultdict
from datetime import UTC, datetime

from skin_radar.config import Settings
from skin_radar.core.matching import can_compare
from skin_radar.core.profit import calculate_costs
from skin_radar.core.quality import quote_age_seconds, score_signal
from skin_radar.models import MarketQuote, Opportunity, QuoteKind


class OpportunityScanner:
    def __init__(self, settings: Settings):
        self.settings = settings

    def scan(self, quotes: list[MarketQuote]) -> list[Opportunity]:
        now = datetime.now(UTC)
        if self.settings.mode == "live":
            verified = self.settings.fx_verified_at
            if verified is None or verified.tzinfo is None:
                return []
            age = (now - verified).total_seconds()
            if age < 0 or age > self.settings.max_fx_age_hours * 3600:
                return []
        fresh = [
            quote
            for quote in quotes
            if quote_age_seconds(quote, now) <= self.settings.max_quote_age_seconds
            and quote.observed_at.tzinfo is not None
            and (quote.observed_at - now).total_seconds() <= 60
            and (self.settings.mode == "demo" or not quote.demo)
            and quote.provider in self.settings.enabled_providers
            and quote.item.market_hash_name not in self.settings.excluded_items
        ]
        grouped: dict[str, list[MarketQuote]] = defaultdict(list)
        for quote in fresh:
            grouped[quote.item.market_hash_name].append(quote)

        candidates: list[Opportunity] = []
        for name, item_quotes in grouped.items():
            asks = [quote for quote in item_quotes if quote.kind == QuoteKind.ACTIVE_LISTING]
            sell_refs = [
                quote
                for quote in item_quotes
                if quote.kind in {QuoteKind.ACTIVE_LISTING, QuoteKind.BUY_ORDER}
            ]
            for buy in asks:
                if buy.price_rub > self.settings.max_item_price_rub:
                    continue
                for sell in sell_refs:
                    if buy.provider == sell.provider or buy.quote_id == sell.quote_id:
                        continue
                    matched, match_warnings = can_compare(buy.item, sell.item)
                    if not matched:
                        continue
                    try:
                        costs = calculate_costs(buy, sell, self.settings)
                    except ValueError:
                        continue
                    if not costs.cashout_eligible:
                        continue
                    if (
                        costs.total_invested_rub
                        > self.settings.budget_rub - self.settings.reserve_rub
                    ):
                        continue
                    if costs.net_profit_rub < self.settings.min_profit_rub:
                        continue
                    if costs.roi_pct < self.settings.min_roi_pct:
                        continue
                    delays = (buy.transfer_delay_hours, sell.transfer_delay_hours)
                    hold_hours = max(delays) if None not in delays else None
                    if hold_hours is not None:
                        for item in (buy.item, sell.item):
                            if item.tradable_after is not None:
                                if item.tradable_after.tzinfo is None:
                                    hold_hours = None
                                    break
                                hold_hours = max(
                                    hold_hours,
                                    math.ceil(
                                        max(0, (item.tradable_after - now).total_seconds()) / 3600
                                    ),
                                )
                    if hold_hours is None and not self.settings.allow_unknown_hold:
                        continue
                    if hold_hours is not None and hold_hours > self.settings.max_hold_hours:
                        continue
                    liq_score, liquidity, risk, confidence, warnings = score_signal(
                        buy, sell, match_warnings
                    )
                    if liquidity.value == "unknown":
                        if not self.settings.allow_unknown_liquidity:
                            continue
                    elif liq_score < self.settings.min_liquidity_score:
                        continue
                    digest = hashlib.sha256(
                        f"{buy.provider}:{buy.quote_id}:{sell.provider}:{sell.quote_id}".encode()
                    ).hexdigest()[:24]
                    candidates.append(
                        Opportunity(
                            fingerprint=digest,
                            item_name=name,
                            buy=buy,
                            sell=sell,
                            costs=costs,
                            liquidity=liquidity,
                            liquidity_score=liq_score,
                            risk_score=risk,
                            confidence=confidence,
                            hold_hours=hold_hours,
                            warnings=warnings,
                        )
                    )
        unique = {candidate.fingerprint: candidate for candidate in candidates}
        return sorted(
            unique.values(),
            key=lambda item: (item.costs.net_profit_rub, item.costs.roi_pct),
            reverse=True,
        )
