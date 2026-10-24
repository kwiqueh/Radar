from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from skin_radar.bot import AuthorizedChatMiddleware
from skin_radar.core.scanner import OpportunityScanner
from skin_radar.dashboard import create_app
from skin_radar.providers.base import ProviderResult
from skin_radar.service import RadarService


async def test_dedup_is_atomic_persistent(settings, quotes):
    service = RadarService(settings)
    offer = OpportunityScanner(settings).scan(quotes)[0]
    assert service.storage.claim_alert(offer, "telegram")
    assert not service.storage.claim_alert(offer, "telegram")
    assert len(service.storage.alerts()) == 1
    await service.close()
    reopened = RadarService(settings)
    assert not reopened.storage.claim_alert(offer, "telegram")
    await reopened.close()


async def test_demo_never_automatically_sends(settings):
    service = RadarService(settings)
    service.notifier.send = AsyncMock()
    assert await service.scan()
    service.notifier.send.assert_not_called()
    assert service.storage.stats()["latest_scan"]["quote_count"] == 3
    assert service.storage.history("AK-47 | Redline (Field-Tested)")
    await service.close()


async def test_provider_failure_does_not_hide_healthy_provider(settings, quotes):
    service = RadarService(settings)
    good, bad = AsyncMock(), AsyncMock()
    good.name, bad.name = "good", "bad"
    good.fetch.return_value = ProviderResult(quotes, "ok")
    bad.fetch.side_effect = RuntimeError("secret error details")
    service.providers = lambda: [good, bad]
    await service.scan()
    states = {s.name: s for s in service.storage.provider_states()}
    assert states["good"].healthy and not states["bad"].healthy
    assert "secret" not in states["bad"].detail
    await service.scan()
    assert good.fetch.await_count == 1  # manual /scan does not bypass the API cooldown
    await service.close()


def test_dashboard_scan_filters_history_security(settings):
    service = RadarService(settings)
    with TestClient(create_app(service), base_url="http://127.0.0.1") as client:
        assert client.get("/").status_code == 200
        assert client.post("/api/scan").status_code == 200
        offers = client.get("/api/opportunities").json()
        assert len(offers) == 1 and offers[0]["buy"]["demo"]
        assert client.get("/api/history", params={"item_name": offers[0]["item_name"]}).json()
        assert client.post("/api/filters", json={"budget_rub": "1000"}).status_code == 422
        assert service.settings.budget_rub == 5000
        assert client.post("/api/filters", json={"min_profit_rub": "500"}).status_code == 200
        assert client.post("/api/scan").json()["count"] == 0
        assert client.get("/api/opportunities").json() == []
        assert (
            client.post("/api/scan", headers={"Origin": "https://evil.example"}).status_code == 403
        )
        assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


async def test_bot_rejects_other_chats():
    from types import SimpleNamespace

    middleware = AuthorizedChatMiddleware("123")
    handler = AsyncMock()
    await middleware(handler, SimpleNamespace(chat=SimpleNamespace(id=999)), {})
    handler.assert_not_called()
    await middleware(handler, SimpleNamespace(chat=SimpleNamespace(id=123)), {})
    handler.assert_awaited_once()


async def test_filter_batch_is_transactional(settings):
    service = RadarService(settings)
    with pytest.raises(ValueError):
        service.update_filters({"min_profit_rub": "200", "budget_rub": "100"})
    assert not service.storage.filters()
    assert service.settings.min_profit_rub == 150
    await service.close()


@pytest.mark.parametrize("command", ["start", "scan", "top", "filters", "stats", "status", "help"])
async def test_bot_commands_offline(settings, command):
    from datetime import UTC, datetime

    from aiogram import Bot, Dispatcher
    from aiogram.types import Update

    from skin_radar.bot import build_router

    settings = settings.model_copy(update={"telegram_chat_id": "123"})
    service = RadarService(settings)
    await service.scan()
    # Deliberately fake token; the transport is mocked before handling any update.
    bot = Bot(f"{123456}:FAKE_TOKEN_FOR_OFFLINE_TEST")
    bot.session.make_request = AsyncMock(return_value=None)
    dispatcher = Dispatcher()
    dispatcher.include_router(build_router(service))
    update = Update.model_validate(
        {
            "update_id": 1,
            "message": {
                "message_id": 1,
                "date": datetime.now(UTC),
                "chat": {"id": 123, "type": "private"},
                "from": {"id": 123, "is_bot": False, "first_name": "Tester"},
                "text": f"/{command}",
            },
        }
    )
    try:
        await dispatcher.feed_update(bot, update)
        assert bot.session.make_request.await_count >= 1
    finally:
        await bot.session.close()
        await service.close()
