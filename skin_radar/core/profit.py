from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from skin_radar.config import Settings, VenueFees
from skin_radar.models import CostBreakdown, MarketQuote

HUNDRED = Decimal("100")
CENT = Decimal("0.01")


def money(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def calculate_costs(buy: MarketQuote, sell: MarketQuote, settings: Settings) -> CostBreakdown:
    demo_fees = VenueFees(
        purchase_pct=settings.purchase_fee_pct,
        funding_pct=settings.funding_fee_pct,
        seller_pct=settings.seller_fee_pct,
        withdrawal_pct=settings.withdrawal_fee_pct,
        cashout_available=True,
        verified=True,
        note="DEMO assumptions only",
    )
    fallback = demo_fees if settings.mode == "demo" else VenueFees()
    purchase = (
        demo_fees if settings.mode == "demo" else settings.venue_fees.get(buy.provider, fallback)
    )
    sale = (
        demo_fees if settings.mode == "demo" else settings.venue_fees.get(sell.provider, fallback)
    )
    if settings.mode == "live" and (not purchase.verified or not sale.verified):
        raise ValueError("Verify fee profiles for both venues before computing live profit")
    purchase_fee = buy.price_rub * purchase.purchase_pct / HUNDRED
    funding_fee = buy.price_rub * purchase.funding_pct / HUNDRED + purchase.funding_fixed_rub
    unexpected = buy.price_rub * settings.unexpected_cost_reserve_pct / HUNDRED
    invested = buy.price_rub + purchase_fee + funding_fee + unexpected + settings.fixed_cost_rub

    seller_fee = sell.price_rub * sale.seller_pct / HUNDRED
    after_seller = sell.price_rub - seller_fee
    withdrawal_fee = after_seller * sale.withdrawal_pct / HUNDRED + sale.withdrawal_fixed_rub
    proceeds = after_seller - withdrawal_fee
    profit = proceeds - invested
    roi = profit / invested * HUNDRED if invested else Decimal("0")
    return CostBreakdown(
        purchase_price_rub=money(buy.price_rub),
        purchase_fee_rub=money(purchase_fee),
        funding_fee_rub=money(funding_fee),
        unexpected_reserve_rub=money(unexpected),
        total_invested_rub=money(invested),
        sale_price_rub=money(sell.price_rub),
        seller_fee_rub=money(seller_fee),
        withdrawal_fee_rub=money(withdrawal_fee),
        net_proceeds_rub=money(proceeds),
        fixed_cost_rub=money(settings.fixed_cost_rub),
        net_profit_rub=money(profit),
        roi_pct=roi.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP),
        withdrawal_minimum_rub=sale.withdrawal_minimum_rub,
        cashout_eligible=sale.cashout_available and proceeds >= sale.withdrawal_minimum_rub,
    )
