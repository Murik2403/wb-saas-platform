"""In-memory MarketplaceClient for tests — never makes network calls."""
from __future__ import annotations

from agents.marketplaces.base import ApplyResult, Campaign, PriceRow


class MockClient:
    marketplace = "mock"

    def __init__(self, prices: list[PriceRow] | None = None, campaigns: list[Campaign] | None = None):
        self._prices = prices or []
        self._campaigns = campaigns or []
        self.calls: list[tuple] = []

    def get_prices(self) -> list[PriceRow]:
        return self._prices

    def set_price(self, sku: str, price: float, *, dry_run: bool = True) -> ApplyResult:
        self.calls.append(("set_price", sku, price, dry_run))
        if not dry_run:
            raise AssertionError("real (non-dry-run) network call attempted in a test")
        return ApplyResult(ok=True, dry_run=dry_run, detail=f"mock set_price {sku}={price}")

    def get_campaigns(self) -> list[Campaign]:
        return self._campaigns

    def set_campaign_budget(self, campaign_id: str, budget: float, *, dry_run: bool = True) -> ApplyResult:
        self.calls.append(("set_campaign_budget", campaign_id, budget, dry_run))
        if not dry_run:
            raise AssertionError("real (non-dry-run) network call attempted in a test")
        return ApplyResult(ok=True, dry_run=dry_run, detail=f"mock set_budget {campaign_id}={budget}")

    def pause_campaign(self, campaign_id: str, *, dry_run: bool = True) -> ApplyResult:
        self.calls.append(("pause_campaign", campaign_id, dry_run))
        if not dry_run:
            raise AssertionError("real (non-dry-run) network call attempted in a test")
        return ApplyResult(ok=True, dry_run=dry_run, detail=f"mock pause {campaign_id}")

    def resume_campaign(self, campaign_id: str, *, dry_run: bool = True) -> ApplyResult:
        self.calls.append(("resume_campaign", campaign_id, dry_run))
        if not dry_run:
            raise AssertionError("real (non-dry-run) network call attempted in a test")
        return ApplyResult(ok=True, dry_run=dry_run, detail=f"mock resume {campaign_id}")
