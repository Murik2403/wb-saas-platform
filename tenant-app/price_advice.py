"""Cautious price RECOMMENDATIONS ("репрайсер без автоматики"). Pure calculation.

Nothing here talks to WB or writes anything. The output is advice that the UI
may copy into the existing price DRAFT; sending to WB stays a separate,
confirmed, manual action.

Model (per SKU, over the selected period; everything comes from the
``financial_products`` frame of ``calculations.build_dashboard``, i.e. the same
numbers the "Товары" table shows):

* units U   = "Продано нетто" (sales minus returns);
* p         = realised average price per unit = buyer_revenue / U
              ("Выкупы по цене покупателя", the price after the seller discount);
* k         = price-proportional share of the price = 1 - to_pay / buyer_revenue,
              where to_pay ("К перечислению за товар", ppvz_for_pay) is what WB pays
              out after its commission and acquiring. So k = WB commission +
              acquiring as a fraction of the price. Only read from data, no
              assumption about category rates. Tax is NOT in the cabinet's net
              profit and is therefore not modelled (margin is before tax).
* F         = fixed-per-unit cost = p*(1-k) - net_profit/U. Calibrated so the
              model reproduces the cabinet's "Чистая прибыль" exactly at price p.
              It contains unit cost (cost/U), WB logistics/storage/penalties
              (wb_expenses/U), advertising (ad_spend/U) and the remainder (the
              allocated share of common expenses, WB additional payments). The
              split is shown to the user; F is NOT scaled with the price.

Unit profit at final price P (after the seller discount): P*(1-k) - F.
Margin(P) = (P*(1-k) - F) / P            (same definition as the table:
                                          net profit / buyer revenue)
Break-even final price = F / (1-k)
Price for target margin m = F / (1-k-m)  (unreachable if 1-k-m <= 0)

Assumptions and limits (all shown to the user):
* demand is not modelled: no elasticity, volume may change after a price change
  ("оценка, спрос на цену не прогнозируется");
* the seller discount d is kept; only the base price changes so that
  base*(1-d/100) hits the target; base is an integer (rounded UP so the target
  is not missed), final = round-half-up(base*(1-d/100)) exactly like the UI;
* we only recommend RAISING a price (never lower it automatically); the step is
  capped at MAX_STEP_PCT of the current base price. After a capped step the
  result may stay below the target or even below break-even; then it is flagged;
* advertising per unit is treated as fixed: ad-heavy SKUs (DRR high) are weak
  because ad spend usually moves with sales, not per unit;
* SKUs are skipped (status insufficient_data) when: fewer than MIN_SALES sales,
  net units or revenue <= 0, cost missing/incomplete, k outside 2..60 %,
  unreachable target, no current price, or net profit unknown;
* SKUs with a price upload still in progress get status upload_in_progress.
"""
from __future__ import annotations

import math
from typing import Any

MAX_STEP_PCT = 15.0   # max single-step base-price change
MIN_SALES = 10        # minimal sales (units) in the window
MIN_K, MAX_K = 0.02, 0.60


def _num(v: Any) -> float | None:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def final_price(base: int, discount: int) -> int:
    """Price after the seller discount, rounded half-up (matches the UI's Math.round)."""
    return int(math.floor(base * (1 - discount / 100) + 0.5))


def margin_pct(final: float, k: float, fixed: float) -> float:
    return (final * (1 - k) - fixed) / final * 100


def base_for_final(target_final: float, discount: int) -> int:
    """Smallest integer base price whose discounted price is >= target_final."""
    base = max(1, math.ceil(target_final / (1 - discount / 100) - 1e-9))
    while final_price(base, discount) < math.ceil(target_final - 1e-9):  # half-up rounding safety
        base += 1
    return base


def advise_sku(
    row: dict,
    current_price: Any,
    current_discount: Any,
    target_margin: float,
    *,
    upload_in_progress: bool = False,
    max_step_pct: float = MAX_STEP_PCT,
    min_sales: int = MIN_SALES,
) -> dict:
    """`row` keys: nm_id, vendor_code, sale_units, units, revenue, to_pay, net_profit,
    cost, unsold_cost_units, ad_spend, wb_expenses (all period totals)."""
    price, disc = _num(current_price), _num(current_discount)
    out: dict = {
        "nm_id": row.get("nm_id"), "vendor_code": row.get("vendor_code") or "",
        "current_price": int(price) if price is not None else None,
        "current_discount": int(disc) if disc is not None else None,
        "current_final": None, "recommended_price": None, "recommended_final": None,
        "est_margin_now_pct": None, "est_margin_after_pct": None, "breakeven_final": None,
        "realised_margin_pct": None, "reason": "", "status": "insufficient_data", "note": "",
    }

    def skip(note: str, status: str = "insufficient_data") -> dict:
        out["status"], out["note"], out["reason"] = status, note, note
        return out

    if upload_in_progress:
        return skip("Загрузка цен в WB ещё обрабатывается — дождитесь результата.", "upload_in_progress")
    if price is None or disc is None or price <= 0 or not (0 <= disc <= 99):
        return skip("Нет текущей цены или скидки из WB.")
    price, disc = int(price), int(disc)
    cur_final = final_price(price, disc)
    out["current_final"] = cur_final
    if cur_final <= 0:
        return skip("Цена после скидки равна нулю.")

    sales, units = _num(row.get("sale_units")), _num(row.get("units"))
    revenue, to_pay, net = _num(row.get("revenue")), _num(row.get("to_pay")), _num(row.get("net_profit"))
    if sales is None or sales < min_sales:
        return skip(f"Мало продаж за период: {int(sales or 0)} < {min_sales}. Оценка была бы ненадёжной.")
    if units is None or units <= 0 or revenue is None or revenue <= 0:
        return skip("Нет чистых продаж или выручки за период.")
    if net is None or to_pay is None:
        return skip("Нет чистой прибыли по товару (не сверена бухгалтерия).")
    cost = _num(row.get("cost")) or 0.0
    if cost <= 0 or (_num(row.get("unsold_cost_units")) or 0) > 0:
        return skip("Себестоимость не задана или известна не для всех продаж — маржа была бы завышена.")

    k = 1 - to_pay / revenue
    if not (MIN_K <= k <= MAX_K):
        return skip(f"Не удалось надёжно выделить долю комиссии WB из отчёта ({k * 100:.0f}% цены).")
    p = revenue / units
    fixed = p * (1 - k) - net / units
    m = target_margin / 100
    out["realised_margin_pct"] = round(net / revenue * 100, 1)
    out["breakeven_final"] = int(math.ceil(fixed / (1 - k) - 1e-9)) if fixed > 0 else 0
    now = margin_pct(cur_final, k, fixed)
    out["est_margin_now_pct"] = round(now, 1)

    unit_cost = cost / units
    ads = (_num(row.get("ad_spend")) or 0) / units
    wb_fixed = (_num(row.get("wb_expenses")) or 0) / units
    rest = fixed - unit_cost - ads - wb_fixed
    inputs = (
        f"Средняя цена продажи {p:.0f} ₽; комиссия и эквайринг WB ≈ {k * 100:.1f}% цены. "
        f"На единицу не зависит от цены: себестоимость {unit_cost:.0f} ₽, логистика/хранение WB {wb_fixed:.0f} ₽, "
        f"реклама {ads:.0f} ₽, прочее {rest:.0f} ₽ (итого {fixed:.0f} ₽). "
        f"Безубыточная цена после скидки {out['breakeven_final']} ₽."
    )
    tail = " Оценка, спрос на цену не прогнозируется: объём продаж может измениться."

    if 1 - k - m <= 0:
        return skip(f"Цель {target_margin:g}% недостижима: комиссия WB ≈ {k * 100:.0f}% цены оставляет меньше. " + inputs)

    below = cur_final < out["breakeven_final"]
    if now >= target_margin and not below:
        out["status"] = "ok"
        out["reason"] = f"Маржа по оценке {now:.1f}% — не ниже цели {target_margin:g}%. Менять не нужно. " + inputs + tail
        return out

    # raise only; capped single step on the base price
    want = base_for_final(fixed / (1 - k - m), disc)
    cap_base = int(math.floor(price * (1 + max_step_pct / 100) + 1e-9))
    new_base, capped = (cap_base, True) if want > cap_base else (want, False)
    if new_base <= price:
        out["status"] = "below_breakeven" if below else "ok"
        out["note"] = out["reason"] = "Шаг изменения слишком мал — цена остаётся прежней. " + inputs + tail
        return out
    new_final = final_price(new_base, disc)
    after = margin_pct(new_final, k, fixed)
    out.update(recommended_price=new_base, recommended_final=new_final, est_margin_after_pct=round(after, 1))
    steps = f"Повышение цены {price} → {new_base} ₽ (скидка {disc}% сохранена, цена после скидки {cur_final} → {new_final} ₽)."
    if capped:
        steps += f" Шаг ограничен {max_step_pct:g}% от текущей цены; до цели {target_margin:g}% нужна цена {want} ₽, повторите после проверки."
    if below:
        out["status"] = "below_breakeven"
        warn = f"Ниже безубыточности: цена после скидки {cur_final} ₽ меньше {out['breakeven_final']} ₽, каждая продажа в минус. "
        if new_final < out["breakeven_final"]:
            warn += "Даже после этого шага цена остаётся ниже безубыточности. "
        out["note"] = warn.strip()
        out["reason"] = warn + steps + " " + inputs + tail
    else:
        out["status"] = "recommend"
        out["note"] = f"Маржа {now:.1f}% → {after:.1f}% (цель {target_margin:g}%)."
        out["reason"] = f"Маржа по оценке {now:.1f}%, цель {target_margin:g}%. " + steps + " " + inputs + tail
    return out


def advise_all(rows: list[dict], prices: dict[int, tuple], target_margin: float, in_progress: set[int]) -> list[dict]:
    """`prices`: nm_id -> (price, discount). One item per SKU of `rows`."""
    out = []
    for row in rows:
        price, disc = prices.get(row.get("nm_id"), (None, None))
        out.append(advise_sku(row, price, disc, target_margin, upload_in_progress=row.get("nm_id") in in_progress))
    return out
