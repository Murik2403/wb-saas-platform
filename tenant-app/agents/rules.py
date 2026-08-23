"""Configurable safety-bounded strategies used by price_agent and ad_agent.

Every rule returns a bounded suggestion, never applies anything itself —
price_agent.py / ad_agent.py turn suggestions into agent_candidates rows,
and a human (or an explicit --auto-apply run) decides whether to call
the client's write methods.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class PriceRules:
    min_margin_pct: float = 15.0       # цена никогда не опускается ниже cost * (1 + min_margin_pct/100)
    max_step_pct: float = 5.0          # изменение за один цикл не больше max_step_pct% от текущей цены
    undercut_competitor_pct: float = 1.0  # на сколько % ниже конкурента целимся, если это не нарушает min_margin


@dataclass
class AdRules:
    max_drr_pct: float = 25.0          # целевая доля рекламных расходов; выше — снижаем бюджет/паузим
    min_drr_pct: float = 5.0           # ниже — можно наращивать бюджет (кампания эффективна)
    max_budget_step_pct: float = 20.0  # изменение бюджета за цикл не больше max_budget_step_pct%
    pause_after_days_over_drr: int = 3  # кампания стабильно выше max_drr столько циклов подряд -> предложить паузу


def clamp_price_step(current: float, target: float, max_step_pct: float) -> float:
    """Ограничивает шаг изменения цены за один цикл."""
    max_delta = current * (max_step_pct / 100)
    delta = max(min(target - current, max_delta), -max_delta)
    return round(current + delta, 2)


def clamp_budget_step(current: float, target: float, max_step_pct: float) -> float:
    max_delta = current * (max_step_pct / 100)
    delta = max(min(target - current, max_delta), -max_delta)
    return round(max(current + delta, 0), 2)


def floor_price_by_margin(cost: float | None, min_margin_pct: float) -> float | None:
    if cost is None:
        return None
    return round(cost * (1 + min_margin_pct / 100), 2)
