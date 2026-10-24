from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.responses import JSONResponse

from skin_radar.service import FILTER_FIELDS, RadarService

LOG = logging.getLogger(__name__)
BASE = Path(__file__).resolve().parent


def create_app(service: RadarService) -> FastAPI:
    stop = asyncio.Event()

    async def scan_loop() -> None:
        while not stop.is_set():
            try:
                await service.scan()
            except Exception:
                LOG.exception("Background scan failed")
            try:
                await asyncio.wait_for(stop.wait(), timeout=service.settings.scan_interval_seconds)
            except TimeoutError:
                pass

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        task = asyncio.create_task(scan_loop(), name="skin-radar-scan-loop")
        yield
        stop.set()
        task.cancel()
        with suppress(asyncio.CancelledError):
            await task
        await service.close()

    app = FastAPI(title="Skin Radar", version="0.1.0", lifespan=lifespan)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]"])

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        origin = request.headers.get("origin")
        if (
            request.method == "POST"
            and origin
            and urlsplit(origin).netloc != request.headers.get("host")
        ):
            return JSONResponse({"detail": "Cross-origin changes are forbidden"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; frame-ancestors 'none'"
        )
        return response

    templates = Jinja2Templates(directory=BASE / "templates")
    app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")

    @app.get("/", response_class=HTMLResponse)
    async def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={"demo": service.settings.mode == "demo"},
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "mode": service.settings.mode}

    @app.get("/api/opportunities")
    async def opportunities(limit: int = Query(default=100, ge=1, le=500)) -> list[dict[str, Any]]:
        return [item.model_dump(mode="json") for item in service.storage.opportunities(limit)]

    @app.post("/api/scan")
    async def scan() -> dict[str, Any]:
        items = await service.scan()
        return {"count": len(items), "items": [item.model_dump(mode="json") for item in items]}

    @app.get("/api/status")
    async def status() -> list[dict[str, Any]]:
        return [state.model_dump(mode="json") for state in service.storage.provider_states()]

    @app.get("/api/stats")
    async def stats() -> dict[str, Any]:
        return {**service.storage.stats(), "warnings": service.configuration_warnings()}

    @app.get("/api/alerts")
    async def alerts() -> list[dict[str, Any]]:
        return service.storage.alerts()

    @app.get("/api/history")
    async def history(
        item_name: str, limit: int = Query(default=200, ge=1, le=1000)
    ) -> list[dict[str, Any]]:
        return service.storage.history(item_name, limit)

    @app.get("/api/filters")
    async def filters() -> dict[str, Any]:
        return filter_values()

    def filter_values() -> dict[str, str]:
        return {
            name: ",".join(getattr(service.settings, name))
            if name in {"enabled_providers", "excluded_items"}
            else str(getattr(service.settings, name)).lower()
            for name in FILTER_FIELDS
        }

    @app.post("/api/filters")
    async def update_filters(payload: dict[str, str]) -> dict[str, Any]:
        try:
            service.update_filters(payload)
        except ValueError as exc:
            raise HTTPException(
                status_code=422, detail="Invalid filters; check budget, reserve and item price"
            ) from exc
        return filter_values()

    return app
