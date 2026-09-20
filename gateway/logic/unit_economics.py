"""Pure unit-economics calculator domain.

The public calculator accepts manual or imported values.  This module deliberately
contains no HTTP, database, or UI code so the same contract can be used by the
marketing calculator and the authenticated tenant reports.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Mapping

EXPENSE_ARTICLES = (
    "purchase_production",
    "wb_commission",
    "logistics_returns",
    "storage",
    "acceptance",
    "advertising",
    "packaging_labor",
    "defects_losses",
    "taxes",
)
ALLOWED_ALLOCATION_BASES = ("per_unit", "total", "revenue_pct", "price_pct")
ALLOWED_SOURCES = ("manual", "wb_api", "import", "derived")


class CalculatorValidationError(ValueError):
    """Raised when calculator input cannot be interpreted safely."""


def _decimal(value: Any, field_name: str) -> Decimal:
    if isinstance(value, bool):
        raise CalculatorValidationError(f"{field_name} must be numeric")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise CalculatorValidationError(f"{field_name} must be numeric") from exc
    if not result.is_finite() or result < 0:
        raise CalculatorValidationError(f"{field_name} must be finite and non-negative")
    return result


def _date(value: Any, field_name: str) -> date | None:
    if value in (None, ""):
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError) as exc:
        raise CalculatorValidationError(f"{field_name} must be an ISO date") from exc


@dataclass(frozen=True)
class ExpenseArticle:
    amount_rub: Decimal
    allocation_basis: str = "per_unit"
    source: str = "manual"
    period_start: date | None = None
    period_end: date | None = None

    @classmethod
    def from_value(cls, value: Any, article: str) -> "ExpenseArticle":
        if isinstance(value, cls):
            item = value
        elif isinstance(value, Mapping):
            basis = str(value.get("allocation_basis", "per_unit") or "per_unit")
            source = str(value.get("source", "manual") or "manual")
            start = _date(value.get("period_start"), f"{article}.period_start")
            end = _date(value.get("period_end"), f"{article}.period_end")
            item = cls(_decimal(value.get("amount_rub", 0), f"{article}.amount_rub"), basis, source, start, end)
        else:
            item = cls(_decimal(value, f"{article}.amount_rub"))
        if item.allocation_basis not in ALLOWED_ALLOCATION_BASES:
            raise CalculatorValidationError(f"{article}.allocation_basis is unsupported")
        if item.source not in ALLOWED_SOURCES:
            raise CalculatorValidationError(f"{article}.source is unsupported")
        if item.period_start and item.period_end and item.period_end < item.period_start:
            raise CalculatorValidationError(f"{article}.period_end precedes period_start")
        amount = _decimal(item.amount_rub, f"{article}.amount_rub")
        return cls(amount, item.allocation_basis, item.source, item.period_start, item.period_end)


@dataclass
class CalculatorInput:
    price_rub: Any
    units: Any
    articles: Mapping[str, Any] = field(default_factory=dict)
    frozen_capital_rub: Any = 0
    # Legacy calculator fields.  They remain valid and are translated to article rows.
    cost_per_unit_rub: Any = 0
    commission_pct: Any = 0
    logistics_per_unit_rub: Any = 0
    packaging_per_unit_rub: Any = 0
    advertising_per_unit_rub: Any = 0

    def __post_init__(self) -> None:
        self.price_rub = _decimal(self.price_rub, "price_rub")
        self.units = _decimal(self.units, "units")
        if self.units <= 0:
            raise CalculatorValidationError("units must be greater than zero")
        self.frozen_capital_rub = _decimal(self.frozen_capital_rub, "frozen_capital_rub")
        self.cost_per_unit_rub = _decimal(self.cost_per_unit_rub, "cost_per_unit_rub")
        self.commission_pct = _decimal(self.commission_pct, "commission_pct")
        self.logistics_per_unit_rub = _decimal(self.logistics_per_unit_rub, "logistics_per_unit_rub")
        self.packaging_per_unit_rub = _decimal(self.packaging_per_unit_rub, "packaging_per_unit_rub")
        self.advertising_per_unit_rub = _decimal(self.advertising_per_unit_rub, "advertising_per_unit_rub")
        if self.commission_pct > 100:
            raise CalculatorValidationError("commission_pct must not exceed 100")
        unknown = set(self.articles) - set(EXPENSE_ARTICLES)
        if unknown:
            raise CalculatorValidationError(f"unsupported expense articles: {', '.join(sorted(unknown))}")
        self.articles = {name: ExpenseArticle.from_value(value, name) for name, value in self.articles.items()}


@dataclass(frozen=True)
class CalculatorResult:
    revenue_rub: Decimal
    article_per_unit: dict[str, Decimal]
    article_metadata: dict[str, ExpenseArticle]
    total_expenses_per_unit: Decimal
    profit_per_unit: Decimal
    margin_pct: Decimal
    frozen_capital_rub: Decimal
    frozen_capital_per_unit: Decimal


def _q(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def calculate_unit_economics(data: CalculatorInput) -> CalculatorResult:
    """Calculate per-unit result, keeping frozen capital outside expenses."""
    explicit = dict(data.articles)
    legacy: dict[str, ExpenseArticle] = {}
    if data.cost_per_unit_rub:
        legacy["purchase_production"] = ExpenseArticle(data.cost_per_unit_rub)
    if data.commission_pct:
        legacy["wb_commission"] = ExpenseArticle(data.commission_pct, "price_pct", "manual")
    if data.logistics_per_unit_rub:
        legacy["logistics_returns"] = ExpenseArticle(data.logistics_per_unit_rub)
    if data.packaging_per_unit_rub:
        legacy["packaging_labor"] = ExpenseArticle(data.packaging_per_unit_rub)
    if data.advertising_per_unit_rub:
        legacy["advertising"] = ExpenseArticle(data.advertising_per_unit_rub)
    metadata = {**legacy, **explicit}
    values: dict[str, Decimal] = {}
    revenue = data.price_rub * data.units
    for article, item in metadata.items():
        if item.allocation_basis == "per_unit":
            per_unit = item.amount_rub
        elif item.allocation_basis == "total":
            per_unit = item.amount_rub / data.units
        elif item.allocation_basis == "revenue_pct":
            per_unit = data.price_rub * item.amount_rub / Decimal("100")
        else:  # price_pct
            per_unit = data.price_rub * item.amount_rub / Decimal("100")
        values[article] = _q(per_unit)
    total = _q(sum(values.values(), Decimal("0")))
    profit = _q(data.price_rub - total)
    margin = _q(profit / data.price_rub * Decimal("100")) if data.price_rub else Decimal("0.00")
    return CalculatorResult(
        revenue_rub=_q(revenue), article_per_unit=values, article_metadata=metadata,
        total_expenses_per_unit=total, profit_per_unit=profit, margin_pct=margin,
        frozen_capital_rub=_q(data.frozen_capital_rub),
        frozen_capital_per_unit=_q(data.frozen_capital_rub / data.units),
    )
