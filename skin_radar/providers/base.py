from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from skin_radar.models import MarketQuote


@dataclass(slots=True)
class ProviderResult:
    quotes: list[MarketQuote]
    detail: str


class MarketProvider(ABC):
    name: str

    @abstractmethod
    async def fetch(self) -> ProviderResult:
        raise NotImplementedError

    async def close(self) -> None:
        return None
