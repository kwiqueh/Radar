from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from skin_radar.models import Confidence, Liquidity, MarketQuote, QuoteKind


def quote_age_seconds(quote: MarketQuote, now: datetime | None = None) -> float:
    now = now or datetime.now(UTC)
    observed = quote.observed_at
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=UTC)
    return max(0.0, (now - observed).total_seconds())


def score_signal(
    buy: MarketQuote,
    sell: MarketQuote,
    attribute_warnings: tuple[str, ...],
) -> tuple[int, Liquidity, int, Confidence, tuple[str, ...]]:
    warnings = list(attribute_warnings)
    liquidity_score = 0
    evidence = 0

    volume = sell.volume_24h
    sales = sell.sales_7d
    if volume is not None:
        liquidity_score += min(35, volume * 3)
        evidence += 1
    if sales is not None:
        liquidity_score += min(35, sales)
        evidence += 1
    spread = sell.spread_pct if sell.spread_pct is not None else buy.spread_pct
    if spread is not None:
        liquidity_score += max(0, 10 - min(10, int(spread)))
        evidence += 1

    liquidity_score = min(100, liquidity_score)
    if volume is None and sales is None:
        liquidity_score = 0
        liquidity = Liquidity.UNKNOWN
        warnings.append("нет данных об объёме реальных продаж")
    elif liquidity_score >= 70:
        liquidity = Liquidity.HIGH
    elif liquidity_score >= 35:
        liquidity = Liquidity.MEDIUM
    else:
        liquidity = Liquidity.LOW

    risk = 20
    if sell.kind != QuoteKind.BUY_ORDER:
        risk += 35
        warnings.append("цена продажи — ask, а не исполнимая заявка покупателя")
    if evidence < 2:
        risk += 15
    if sell.listing_count is not None and sell.listing_count >= 50:
        risk += 5
        warnings.append("высокое количество конкурирующих объявлений")
    if buy.listing_created_at is not None:
        created = buy.listing_created_at
        if created.tzinfo and (datetime.now(UTC) - created).total_seconds() > 86400:
            risk += 5
            warnings.append("объявление покупки размещено более суток назад")
    if spread is not None and spread > 10:
        risk += 10
        warnings.append("широкий bid/ask-spread")
    if attribute_warnings:
        risk += 10
    delays = (buy.transfer_delay_hours, sell.transfer_delay_hours)
    if None in delays:
        risk += 15
        warnings.append("срок передачи/перепродажи неизвестен")
    elif max(delays) > 0:
        risk += 10
        warnings.append("возможна задержка передачи или повторной продажи")
    volatility = sell.volatility_pct
    if volatility is not None and volatility > Decimal("10"):
        risk += 10
        warnings.append("повышенная волатильность")
    risk = min(100, risk)

    if sell.demo or buy.demo:
        warnings.append("DEMO: все котировки и метрики синтетические")
    if (
        sell.kind == QuoteKind.BUY_ORDER
        and liquidity_score >= 35
        and (volume or sales)
        and not attribute_warnings
        and None not in delays
        and max(delays) == 0
        and not buy.aggregate
        and not sell.aggregate
        and not buy.demo
        and not sell.demo
    ):
        confidence = Confidence.CONFIRMED
    elif evidence > 0:
        confidence = Confidence.LIMITED
    else:
        confidence = Confidence.INSUFFICIENT
    return liquidity_score, liquidity, risk, confidence, tuple(dict.fromkeys(warnings))
