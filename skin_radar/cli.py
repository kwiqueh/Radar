from __future__ import annotations

import argparse
import asyncio
import logging

import uvicorn

from skin_radar.bot import run_bot
from skin_radar.config import Settings
from skin_radar.dashboard import create_app
from skin_radar.service import RadarService


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description="Skin Radar read-only CS2 market scanner")
    result.add_argument(
        "command", choices=("scan", "serve", "bot", "run"), nargs="?", default="serve"
    )
    result.add_argument("--demo", action="store_true", help="force synthetic data")
    result.add_argument("--live", action="store_true", help="force configured live providers")
    return result


async def one_scan(service: RadarService) -> None:
    try:
        opportunities = await service.scan()
        print(f"Found {len(opportunities)} candidates. Mode: {service.settings.mode.upper()}")
        for item in opportunities[:20]:
            print(
                f"{item.item_name}: {item.buy.provider} -> {item.sell.provider}; "
                f"potential {item.costs.net_profit_rub:+.2f} RUB ({item.costs.roi_pct:+.2f}%); "
                f"confidence={item.confidence.value}, risk={item.risk_score}/100"
            )
        if service.settings.mode == "demo":
            print("DEMO data is synthetic and is not a real trade.")
        for state in service.storage.provider_states():
            print(f"{state.name}: {state.detail} ({state.quote_count} quotes)")
        for warning in service.configuration_warnings():
            print(warning)
        if service.settings.mode == "live" and not any(
            s.healthy for s in service.storage.provider_states()
        ):
            raise SystemExit(2)
    finally:
        await service.close()


async def run_all(service: RadarService) -> None:
    app = create_app(service)
    config = uvicorn.Config(app, host=service.settings.host, port=service.settings.port)
    server = uvicorn.Server(config)
    tasks = [asyncio.create_task(server.serve())]
    if service.settings.telegram_bot_token:
        tasks.append(asyncio.create_task(run_bot(service)))
    try:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            task.result()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def bot_only(service: RadarService) -> None:
    async def scheduled():
        while True:
            await service.scan()
            await asyncio.sleep(max(370, service.settings.scan_interval_seconds))

    task = asyncio.create_task(scheduled())
    try:
        await run_bot(service)
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        await service.close()


def main() -> None:
    args = parser().parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    settings = Settings()
    if args.demo and args.live:
        raise SystemExit("Choose only one: --demo or --live")
    if args.demo:
        settings = settings.model_copy(update={"mode": "demo"})
    if args.live:
        settings = settings.model_copy(update={"mode": "live"})
    service = RadarService(settings)
    if args.command == "scan":
        asyncio.run(one_scan(service))
    elif args.command == "bot":
        asyncio.run(bot_only(service))
    elif args.command == "run":
        asyncio.run(run_all(service))
    else:
        uvicorn.run(create_app(service), host=settings.host, port=settings.port)


if __name__ == "__main__":
    main()
