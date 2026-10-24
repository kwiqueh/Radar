from __future__ import annotations

from skin_radar.providers.base import MarketProvider, ProviderResult


class DisabledProvider(MarketProvider):
    def __init__(self, name: str, reason: str):
        self.name = name
        self.reason = reason

    async def fetch(self) -> ProviderResult:
        return ProviderResult([], self.reason)
