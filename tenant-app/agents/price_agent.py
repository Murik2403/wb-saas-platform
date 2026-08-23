"""Price agent: proposes price changes, never applies them silently.

evaluate() reads current prices via a MarketplaceClient, applies
PriceRules, and writes candidates to AgentStore. apply_candidate()
is the only path that calls client.set_price(), and always requires
an explicit decided_by ("human" from a UI click, or "auto" only when
the caller passed --auto-apply).
"""
from __future__ import annotations

from .marketplaces.base import ApplyResult, MarketplaceClient, PriceRow
from .rules import PriceRules, clamp_price_step, floor_price_by_margin
from .store import AgentStore


def evaluate(
    client: MarketplaceClient, store: AgentStore, rules: PriceRules = PriceRules(),
    cost_lookup: dict[str, float] | None = None,
) -> list[int]:
    """Fetch current prices, propose changes, return new candidate ids.

    cost_lookup maps sku -> cost, for callers that have cost data the
    marketplace API itself doesn't provide (e.g. MARKETSHELPER's own
    per-nm_id `costs` table). Rows without a matching entry keep
    row.cost as-is (None from every current client).
    """
    candidate_ids: list[int] = []
    for row in client.get_prices():
        if cost_lookup and row.sku in cost_lookup:
            row.cost = cost_lookup[row.sku]
        proposal = _propose(row, rules)
        if proposal is None:
            continue
        target_price, reason = proposal
        candidate_ids.append(
            store.add_candidate(
                agent="price",
                marketplace=client.marketplace,
                target_id=row.sku,
                target_name=row.name,
                action="set_price",
                current_value=row.current_price,
                proposed_value=target_price,
                reason=reason,
            )
        )
    return candidate_ids


def _propose(row: PriceRow, rules: PriceRules) -> tuple[float, str] | None:
    floor = floor_price_by_margin(row.cost, rules.min_margin_pct)

    target = row.current_price
    reasons: list[str] = []

    if row.competitor_price is not None:
        competitor_target = round(row.competitor_price * (1 - rules.undercut_competitor_pct / 100), 2)
        if competitor_target < target:
            target = competitor_target
            reasons.append(
                f"конкурент {row.competitor_price}₽, целимся на -{rules.undercut_competitor_pct}%"
            )

    if floor is not None and target < floor:
        target = floor
        reasons.append(f"ограничено минимальной маржой {rules.min_margin_pct}% (себестоимость {row.cost}₽)")

    if round(target, 2) == round(row.current_price, 2):
        return None

    target = clamp_price_step(row.current_price, target, rules.max_step_pct)
    if round(target, 2) == round(row.current_price, 2):
        return None

    reasons.append(f"шаг ограничен {rules.max_step_pct}% за цикл")
    return target, "; ".join(reasons)


def apply_candidate(
    client: MarketplaceClient, store: AgentStore, candidate: dict, *, decided_by: str, dry_run: bool = True
) -> ApplyResult:
    import json

    price = json.loads(candidate["proposed_value"])
    result = client.set_price(candidate["target_id"], price, dry_run=dry_run)
    store.record_decision(candidate["id"], decided_by=decided_by, outcome="applied" if result.ok else "rejected")
    store.log_apply(agent="price", marketplace=client.marketplace, dry_run=dry_run, ok=result.ok, detail=result.detail)
    return result
