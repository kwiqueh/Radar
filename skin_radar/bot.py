from __future__ import annotations

from aiogram import BaseMiddleware, Bot, Dispatcher, Router
from aiogram.filters import Command
from aiogram.types import BotCommand, Message

from skin_radar.alerts.telegram import format_opportunity
from skin_radar.service import FILTER_FIELDS, RadarService


class AuthorizedChatMiddleware(BaseMiddleware):
    def __init__(self, chat_id: str):
        self.chat_id = chat_id

    async def __call__(self, handler, event, data):
        if not self.chat_id or str(event.chat.id) != self.chat_id:
            return None
        return await handler(event, data)


def build_router(service: RadarService) -> Router:
    router = Router()
    router.message.outer_middleware(AuthorizedChatMiddleware(service.settings.telegram_chat_id))

    @router.message(Command("start"))
    async def start(message: Message) -> None:
        await message.answer("SKIN RADAR готов. Команды: /scan /top /filters /stats /status /help")

    @router.message(Command("scan"))
    async def scan(message: Message) -> None:
        await message.answer("Сканирование запущено…")
        offers = await service.scan()
        await message.answer(f"Готово. Найдено кандидатов: {len(offers)}")

    @router.message(Command("top"))
    async def top(message: Message) -> None:
        offers = service.current_opportunities(5)
        if not offers:
            await message.answer("Кандидатов пока нет. Используйте /scan.")
            return
        for item in offers:
            await message.answer(
                format_opportunity(item, service.settings.mode == "demo"), parse_mode="HTML"
            )

    @router.message(Command("filters"))
    async def filters(message: Message) -> None:
        text = (message.text or "").partition(" ")[2].strip()
        if text:
            if "=" not in text:
                await message.answer("Формат: /filters min_profit_rub=200")
                return
            name, value = (part.strip() for part in text.split("=", 1))
            try:
                service.update_filter(name, value)
            except ValueError as exc:
                await message.answer(str(exc))
                return
            await message.answer(f"Фильтр {name} обновлён: {value}")
            return
        lines = ["Текущие фильтры:"]
        for name in FILTER_FIELDS:
            lines.append(f"{name}={getattr(service.settings, name)}")
        lines.append("\nИзменение: /filters min_profit_rub=200")
        await message.answer("\n".join(lines))

    @router.message(Command("stats"))
    async def stats(message: Message) -> None:
        data = service.storage.stats()
        await message.answer(
            f"Сканирований: {data['scan_count']}\n"
            f"Уведомлений: {data['alert_count']}\n"
            f"Последнее: {data['latest_scan'] or 'ещё не запускалось'}"
        )

    @router.message(Command("status"))
    async def status(message: Message) -> None:
        states = service.storage.provider_states()
        if not states:
            await message.answer("Статус появится после первого /scan.")
            return
        await message.answer(
            "\n".join(
                f"{'✅' if state.healthy else '⛔'} {state.name}: {state.detail}"
                for state in states
            )
        )

    @router.message(Command("help"))
    async def help_command(message: Message) -> None:
        await message.answer(
            "Сканер только читает котировки и не совершает сделок. "
            "Ask другой площадки не является гарантированной ценой продажи. "
            "DEMO-режим использует синтетические данные."
        )

    return router


async def run_bot(service: RadarService) -> None:
    if not service.settings.telegram_bot_token or not service.settings.telegram_chat_id:
        raise RuntimeError("Set both TELEGRAM_BOT_TOKEN and authorized TELEGRAM_CHAT_ID")
    bot = Bot(service.settings.telegram_bot_token)
    await bot.set_my_commands(
        [
            BotCommand(command=name, description=description)
            for name, description in (
                ("start", "Главное меню"),
                ("scan", "Запустить сканирование"),
                ("top", "Лучшие кандидаты"),
                ("filters", "Показать или изменить фильтры"),
                ("stats", "Статистика"),
                ("status", "Состояние источников"),
                ("help", "Справка"),
            )
        ]
    )
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(service))
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()
