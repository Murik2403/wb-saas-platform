"""Marketplace-agnostic interface shared by WB and Ozon clients.

price_agent.py / ad_agent.py talk only to this protocol, never to a
concrete client, so the same decision logic runs against either
marketplace (and against a mock client in tests).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol


@dataclass
class PriceRow:
    sku: str
    name: str
    current_price: float
    cost: float | None = None
    stock: int | None = None
    competitor_price: float | None = None


@dataclass
class Campaign:
    campaign_id: str
    name: str
    status: str  # "active" | "paused"
    daily_budget: float | None
    spend_7d: float
    revenue_7d: float
    ctr: float | None = None

    @property
    def drr(self) -> float | None:
        """Доля рекламных расходов = spend / revenue."""
        if not self.revenue_7d:
            return None
        return self.spend_7d / self.revenue_7d


@dataclass
class ApplyResult:
    ok: bool
    dry_run: bool
    detail: str
    applied_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class MarketplaceClient(Protocol):
    """Implemented by wb_client.WBAgentClient and ozon_client.OzonAgentClient."""

    marketplace: str  # "wb" | "ozon"

    def get_prices(self) -> list[PriceRow]: ...

    def set_price(self, sku: str, price: float, *, dry_run: bool = True) -> ApplyResult: ...

    def get_campaigns(self) -> list[Campaign]: ...

    def set_campaign_budget(
        self, campaign_id: str, budget: float, *, dry_run: bool = True
    ) -> ApplyResult: ...

    def pause_campaign(self, campaign_id: str, *, dry_run: bool = True) -> ApplyResult: ...

    def resume_campaign(self, campaign_id: str, *, dry_run: bool = True) -> ApplyResult: ...
