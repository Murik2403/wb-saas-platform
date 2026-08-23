"""Ad agent: proposes budget/pause changes based on ДРР (spend/revenue), never applies silently.

Same pattern as price_agent.py: evaluate() writes candidates,
apply_candidate() is the only path that mutates a real campaign.
"""
from __future__ import annotations

import json

from .marketplaces.base import ApplyResult, Campaign, MarketplaceClient
from .rules import AdRules, clamp_budget_step
from .store import AgentStore


def evaluate(client: MarketplaceClient, store: AgentStore, rules: AdRules = AdRules()) -> list[int]:
    candidate_ids: list[int] = []
    for campaign in client.get_campaigns():
        proposal = _propose(campaign, rules)
        if proposal is None:
            continue
        action, current, target, reason = proposal
        candidate_ids.append(
            store.add_candidate(
                agent="ad",
                marketplace=client.marketplace,
                target_id=campaign.campaign_id,
                target_name=campaign.name,
                action=action,
                current_value=current,
                proposed_value=target,
                reason=reason,
            )
        )
    return candidate_ids


def _propose(campaign: Campaign, rules: AdRules) -> tuple[str, object, object, str] | None:
    if campaign.status != "active":
        return None

    drr = campaign.drr
    if drr is None:
        return None  # нет продаж за период — недостаточно данных для решения

    drr_pct = drr * 100

    spend_revenue = f"расход {campaign.spend_7d:.0f}₽ / выручка {campaign.revenue_7d:.0f}₽"
    ctr_part = f", CTR {campaign.ctr:.1f}%" if campaign.ctr is not None else ""

    if drr_pct > rules.max_drr_pct:
        if campaign.daily_budget:
            target_budget = clamp_budget_step(
                campaign.daily_budget, campaign.daily_budget * 0.7, rules.max_budget_step_pct
            )
            return (
                "set_budget", campaign.daily_budget, target_budget,
                f"ДРР {drr_pct:.1f}% выше целевых {rules.max_drr_pct}% ({spend_revenue}{ctr_part}) — снижаем бюджет",
            )
        return (
            "pause", campaign.status, "paused",
            f"ДРР {drr_pct:.1f}% выше {rules.max_drr_pct}% ({spend_revenue}{ctr_part}), бюджет не задан",
        )

    if drr_pct < rules.min_drr_pct and campaign.daily_budget:
        target_budget = clamp_budget_step(
            campaign.daily_budget, campaign.daily_budget * 1.3, rules.max_budget_step_pct
        )
        return (
            "set_budget", campaign.daily_budget, target_budget,
            f"ДРР {drr_pct:.1f}% ниже {rules.min_drr_pct}% ({spend_revenue}{ctr_part}) — кампания эффективна, наращиваем бюджет",
        )

    return None


def apply_candidate(
    client: MarketplaceClient, store: AgentStore, candidate: dict, *, decided_by: str, dry_run: bool = True
) -> ApplyResult:
    action = candidate["action"]
    target_id = candidate["target_id"]
    proposed = json.loads(candidate["proposed_value"])

    if action == "set_budget":
        result = client.set_campaign_budget(target_id, proposed, dry_run=dry_run)
    elif action == "pause":
        result = client.pause_campaign(target_id, dry_run=dry_run)
    elif action == "resume":
        result = client.resume_campaign(target_id, dry_run=dry_run)
    else:
        raise ValueError(f"неизвестное действие рекламного агента: {action}")

    store.record_decision(candidate["id"], decided_by=decided_by, outcome="applied" if result.ok else "rejected")
    store.log_apply(agent="ad", marketplace=client.marketplace, dry_run=dry_run, ok=result.ok, detail=result.detail)
    return result
