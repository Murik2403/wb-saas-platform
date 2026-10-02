"""Document-based management estimates, never inferred tax eligibility or declarations."""
from decimal import Decimal, ROUND_HALF_UP

REGIMES = {"ausn_income": 8, "ausn_income_expense": 20, "usn_income": 6,
           "usn_income_expense": 15, "osno_ip": None, "osno_company": 25}
EXTRA_MONEY = ("expenses", "loss", "insurance", "vat_output", "vat_input", "advances", "other_ndfl_base")
BASE_FIELDS = ("received", "offset", "compensation", "other_income", "refunds")


def normalize_options(values):
    v = dict(values)
    regime = v.get("regime", "ausn_income")
    if regime not in REGIMES:
        raise ValueError("Выберите один из шести налоговых режимов")
    ausn = regime.startswith("ausn")
    mode = v.get("period_mode") or ("month" if ausn else "ytd")
    if mode != ("month" if ausn else "ytd"):
        raise ValueError("АУСН: календарный месяц; УСН и ОСНО: с начала года")
    year = int(v["month"][:4])
    if not ausn and year < 2025:
        raise ValueError("Для УСН и ОСНО поддержаны правила начиная с 2025 года")
    taxpayer = v.get("taxpayer_type", "company" if regime == "osno_company" else "ip")
    if taxpayer not in {"ip", "company"} or (regime == "osno_ip" and taxpayer != "ip") or (regime == "osno_company" and taxpayer != "company"):
        raise ValueError("Тип налогоплательщика не соответствует режиму")
    v["taxpayer_type"] = taxpayer
    rate = v.get("rate")
    rate = REGIMES[regime] if rate is None else rate
    if rate is not None:
        number = Decimal(str(rate))
        if not number.is_finite() or number != number.quantize(Decimal("0.01")):
            raise ValueError("Ставка: конечное число с точностью до двух знаков")
    if regime == "osno_ip":
        if rate is not None:
            raise ValueError("Для ИП используется прогрессивная шкала НДФЛ, не фиксированная ставка")
    elif not Decimal(0) <= Decimal(str(rate)) <= Decimal(str(REGIMES[regime])):
        raise ValueError("Ставка вне допустимого диапазона выбранного режима")
    if ausn and rate != REGIMES[regime]:
        raise ValueError("Ставки АУСН фиксированы: 8% или 20%")
    vat = v.get("vat_mode", "exempt")
    allowed = {"exempt"} if ausn else {"exempt", "general0", "general10", "general20" if year == 2025 else "general22"}
    if regime.startswith("usn"):
        allowed |= {"special5", "special7"}
    if vat not in allowed:
        raise ValueError("Режим НДС не соответствует выбранному режиму или году")
    if not vat.startswith("general") and (v.get("vat_input") or 0) != 0:
        raise ValueError("При освобождении и ставках НДС 5/7% входящий НДС к вычету не применяется")
    if vat == "exempt" and (v.get("vat_output") or 0) != 0:
        raise ValueError("При освобождении исходящий НДС должен быть нулём")
    if regime == "usn_income_expense" and v["month"][5:] != "12" and (v.get("loss") or 0) != 0:
        raise ValueError("Убытки прошлых лет УСН учитываются при годовом расчёте в декабре")
    v.update(period_mode=mode, rate=rate, vat_mode=vat, has_staff=bool(v.get("has_staff", False)) or taxpayer == "company")
    return v


def _ndfl(base):
    tax = Decimal(0)
    previous = Decimal(0)
    for limit, rate in ((2400000, 13), (5000000, 15), (20000000, 18), (50000000, 20), (None, 22)):
        upper = base if limit is None else min(base, Decimal(limit))
        tax += max(upper - previous, Decimal(0)) * Decimal(rate) / 100
        if limit is None or base <= limit:
            break
        previous = Decimal(limit)
    return tax


def calculate_tax(values):
    v = normalize_options(values)
    regime = v["regime"]
    required = list(BASE_FIELDS)
    if regime.endswith("expense") or regime.startswith("osno"):
        required += ["expenses"]
        if regime != "osno_ip" and (regime != "usn_income_expense" or v["month"].endswith("-12")):
            required += ["loss"]
    if regime == "usn_income":
        required += ["insurance"]
    if regime == "osno_ip":
        required += ["other_ndfl_base"]
    if v["vat_mode"] != "exempt":
        required += ["vat_output"]
        if v["vat_mode"].startswith("general"):
            required += ["vat_input"]
    missing = [k for k in required if v.get(k) is None]
    result = dict(status="incomplete" if missing else "estimate", missing=missing, rate=v["rate"],
                  base=None, income=None, estimate=None, income_tax=None, minimum_tax=None,
                  vat_to_pay=None, vat_credit=None, insurance_used=None, loss_used=None,
                  income_tax_to_pay=None, advance_excess=None, negative_base=False,
                  period_mode=v["period_mode"], annual=v["period_mode"] == "ytd" and v["month"].endswith("-12"))
    if missing:
        return result
    d = lambda k: Decimal(str(v.get(k) or 0))
    money = lambda x: float(x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
    income = sum((d(k) for k in BASE_FIELDS[:-1]), Decimal(0)) - d("refunds")
    base = income
    loss_used = Decimal(0)
    if regime.endswith("expense") or regime.startswith("osno"):
        base -= d("expenses")
        if regime != "osno_ip":
            cap = max(base, Decimal(0)) * (Decimal("0.5") if regime == "osno_company" else 1)
            loss_used = min(d("loss"), cap)
            base -= loss_used
        elif d("loss") != 0:
            raise ValueError("Убытки прошлых лет не уменьшают НДФЛ ИП")
    positive = max(base, Decimal(0))
    if regime == "osno_ip":
        tax = _ndfl(positive + d("other_ndfl_base")) - _ndfl(d("other_ndfl_base"))
    else:
        tax = positive * Decimal(str(v["rate"])) / 100
    minimum = Decimal(0)
    if regime == "ausn_income_expense":
        minimum = max(income, Decimal(0)) * Decimal("0.03")
    elif regime == "usn_income_expense" and result["annual"] and v["rate"] != 0:
        minimum = max(income, Decimal(0)) * Decimal("0.01")
    tax = max(tax, minimum)
    insurance = Decimal(0)
    if regime == "usn_income":
        insurance = min(d("insurance"), tax * (Decimal("0.5") if v["has_staff"] else 1))
        tax -= insurance
    vat_net = d("vat_output") - d("vat_input") if v["vat_mode"].startswith("general") else d("vat_output") if v["vat_mode"] != "exempt" else Decimal(0)
    vat_pay = max(vat_net, Decimal(0))
    # Advance payments change settlement only, never the expense for profit calculation.
    result.update(base=money(base), income=money(income), income_tax=money(tax), minimum_tax=money(minimum),
                  estimate=money(tax + vat_pay), vat_to_pay=money(vat_pay), vat_credit=money(max(-vat_net, Decimal(0))),
                  insurance_used=money(insurance), loss_used=money(loss_used), negative_base=base < 0)
    if v.get("advances") is not None:
        result.update(income_tax_to_pay=money(max(tax - d("advances"), Decimal(0))),
                      advance_excess=money(max(d("advances") - tax, Decimal(0))))
    return result
