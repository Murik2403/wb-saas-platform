"""Tax estimate on top of the monthly management P&L (not accounting).

Inputs per month come from ``monthly_pnl.monthly_pnl`` rows:
  income   = ``sales``  (the "Фактические продажи" line: retail_amount, i.e. what WB reports as sold
             to buyers; gross fallback when the period has no retail_amount)
  expenses = commission + logistics + storage + penalties + acquiring + advertising + other
  cost     = ``cost``   (FIFO cost of goods; deductible only if ``cost_official``)
  result   = ``result`` (management result before taxes, unchanged)

Formulas (rates in percent, kopeks rounded only at the very end):
  vat_out = income * vat / (100 + vat)            # VAT included in income
  usn_income:          base = income - vat_out;                         usn = base * usn%
  usn_income_expense:  base = income - vat_out - expenses [- cost];     usn = max(base, 0) * usn%
  total = vat_out + usn;  profit_after_tax_estimate = result - total

Not modelled: input VAT, the 1%-of-income minimum tax, loss carry-forward, compensations as
income. A month without both rates is never assumed to be 0: status "rates_missing".
"""
from __future__ import annotations

from db import connect

REGIMES = ("usn_income", "usn_income_expense")
_EXPENSE_FIELDS = ("commission", "logistics", "storage", "penalties", "acquiring", "advertising", "other")


def estimate_month(regime: str, cost_official: bool, income: float, expenses: float, cost: float,
                   usn_pct: float | None, vat_pct: float | None, result: float) -> dict:
    if regime not in REGIMES:
        raise ValueError("unsupported_regime")
    if usn_pct is None or vat_pct is None:
        return {"status": "rates_missing", "usn_base": None, "vat_out": None, "usn": None,
                "total": None, "profit_after_tax_estimate": None, "loss": False}
    vat_out = income * vat_pct / (100 + vat_pct)
    base = income - vat_out
    if regime == "usn_income_expense":
        base -= expenses + (cost if cost_official else 0.0)
    loss = base < 0
    usn = max(base, 0.0) * usn_pct / 100
    total = vat_out + usn
    return {"status": "ok", "usn_base": round(base, 2), "vat_out": round(vat_out, 2), "usn": round(usn, 2),
            "total": round(total, 2), "profit_after_tax_estimate": round(result - total, 2), "loss": loss}


def read_tax_settings(year: int) -> dict:
    with connect() as conn:
        reg = conn.execute("SELECT regime, cost_official FROM tax_regime WHERE year=?", (year,)).fetchone()
        rates = conn.execute("SELECT month, usn_pct, vat_pct FROM tax_rate WHERE year=? ORDER BY month", (year,)).fetchall()
    return {"year": year,
            "regime": reg["regime"] if reg else None,
            "cost_official": bool(reg["cost_official"]) if reg else False,
            "rates": [{"month": r["month"], "usn_pct": r["usn_pct"], "vat_pct": r["vat_pct"]} for r in rates]}


def save_tax_settings(year: int, regime: str, cost_official: bool, rates: list[dict]) -> None:
    """Replace the year's regime and rates atomically."""
    if regime not in REGIMES:
        raise ValueError("unsupported_regime")
    with connect() as conn:
        conn.execute("INSERT INTO tax_regime(year, regime, cost_official) VALUES (?,?,?) "
                     "ON CONFLICT(year) DO UPDATE SET regime=excluded.regime, cost_official=excluded.cost_official",
                     (year, regime, int(cost_official and regime == "usn_income_expense")))
        conn.execute("DELETE FROM tax_rate WHERE year=?", (year,))
        conn.executemany("INSERT INTO tax_rate(year, month, usn_pct, vat_pct) VALUES (?,?,?,?)",
                         [(year, r["month"], r["usn_pct"], r["vat_pct"]) for r in rates])


def add_tax_block(pnl: dict) -> None:
    """Add an additive ``tax`` key to every month and to ``total`` of a monthly_pnl result."""
    years = {int(r["month"][:4]) for r in pnl["months"]}
    cfg = {y: read_tax_settings(y) for y in years}
    blocks = []
    for r in pnl["months"]:
        c = cfg[int(r["month"][:4])]
        if c["regime"] is None:
            r["tax"] = None
        else:
            rate = {x["month"]: x for x in c["rates"]}.get(int(r["month"][5:]))
            r["tax"] = estimate_month(
                c["regime"], c["cost_official"], r["sales"], sum(r[f] for f in _EXPENSE_FIELDS), r["cost"],
                rate["usn_pct"] if rate else None, rate["vat_pct"] if rate else None, r["result"])
        blocks.append(r["tax"])
    done = [b for b in blocks if b and b["status"] == "ok"]
    if not any(blocks):
        pnl["total"]["tax"] = None
        return
    sums = {k: round(sum(b[k] for b in done), 2) for k in ("usn_base", "vat_out", "usn", "total", "profit_after_tax_estimate")}
    # "partial": some months have no tax block or no rates; sums cover only months with status ok.
    status = "ok" if len(done) == len(blocks) else ("rates_missing" if not done else "partial")
    pnl["total"]["tax"] = {"status": status, **sums, "loss": any(b["loss"] for b in done)}
