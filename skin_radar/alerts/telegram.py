from __future__ import annotations

from html import escape

from skin_radar.models import Opportunity
from skin_radar.providers.http import ResilientHttpClient


def format_opportunity(item: Opportunity, demo: bool = False) -> str:
    prefix = "🧪 DEMO · " if demo else "🔎 "
    certainty = {
        "confirmed": "подтверждённая по доступным данным",
        "limited": "ограниченная",
        "insufficient_data": "недостаточно данных",
    }[item.confidence.value]
    costs = (
        item.costs.total_invested_rub
        - item.costs.purchase_price_rub
        + item.costs.seller_fee_rub
        + item.costs.withdrawal_fee_rub
    )
    lines = [
        f"{prefix}<b>SKIN RADAR | Новый кандидат</b>",
        "",
        f"Предмет: <b>{escape(item.item_name)}</b>",
        f"Покупка: {escape(item.buy.provider)} — {item.costs.purchase_price_rub:.2f} ₽",
        f"Продажа: {escape(item.sell.provider)} — {item.costs.sale_price_rub:.2f} ₽",
        f"Расчётные расходы: {costs:.2f} ₽",
        f"Потенциальная прибыль: <b>{item.costs.net_profit_rub:+.2f} ₽</b>",
        f"ROI: {item.costs.roi_pct:+.2f}%",
        f"Ликвидность: {item.liquidity.value} ({item.liquidity_score}/100)",
        f"Риск: {item.risk_score}/100",
        f"Уверенность: {certainty}",
        "",
        f'<a href="{escape(item.buy.url, quote=True)}">Объявление покупки</a> · '
        f'<a href="{escape(item.sell.url, quote=True)}">Источник цены продажи</a>',
        f"Обновлено: {item.created_at.isoformat(timespec='seconds')}",
    ]
    if item.warnings:
        lines.extend(("", "⚠️ " + escape("; ".join(item.warnings))))
    lines.extend(("", "Расчёт не гарантирует продажу, срок или прибыль."))
    return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, client: ResilientHttpClient):
        self.token = token
        self.chat_id = chat_id
        self.client = client

    @property
    def configured(self) -> bool:
        return bool(self.token and self.chat_id)

    async def send(self, opportunity: Opportunity) -> None:
        if not self.configured:
            raise RuntimeError("Telegram notifier is not configured")
        await self.client.post(
            f"https://api.telegram.org/bot{self.token}/sendMessage",
            data={
                "chat_id": self.chat_id,
                "text": format_opportunity(opportunity),
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )
