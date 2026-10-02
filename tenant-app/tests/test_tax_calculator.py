import pytest
from tax_calculator import calculate_tax, normalize_options

def v(regime="ausn_income", **kw):
    x=dict(month="2026-09",regime=regime,period_mode="month" if regime.startswith("ausn") else "ytd",rate=None,
           received=100000,offset=0,compensation=0,other_income=0,refunds=0,expenses=90000,loss=0,
           insurance=0,has_staff=False,vat_mode="exempt",vat_output=0,vat_input=0,advances=0,other_ndfl_base=0)
    x.update(kw); return normalize_options(x)

@pytest.mark.parametrize("regime,tax",[("ausn_income",8000),("ausn_income_expense",3000),("usn_income",6000),("usn_income_expense",1500),("osno_ip",1300),("osno_company",2500)])
def test_six_regimes(regime,tax):
    assert calculate_tax(v(regime))["estimate"]==tax

def test_minimum_only_at_year_end():
    assert calculate_tax(v("usn_income_expense",expenses=100000))["estimate"]==0
    assert calculate_tax(v("usn_income_expense",expenses=100000,month="2026-12"))["estimate"]==1000

def test_insurance_caps_and_prior_advances():
    assert calculate_tax(v("usn_income",insurance=9000,has_staff=True))["income_tax"]==3000
    x=calculate_tax(v("usn_income",insurance=9000,advances=2000))
    assert x["estimate"]==0 and x["income_tax_to_pay"]==0 and x["advance_excess"]==2000

@pytest.mark.parametrize("base,tax",[(2400000,312000),(5000000,702000),(20000000,3402000),(50000000,9402000),(60000000,11602000)])
def test_ndfl_progressive(base,tax):
    assert calculate_tax(v("osno_ip",received=base,expenses=0))["income_tax"]==tax

def test_ndfl_other_income_occupies_lower_brackets():
    assert calculate_tax(v("osno_ip",received=100000,expenses=0,other_ndfl_base=2400000))["income_tax"]==15000

def test_loss_limit_company_and_usn_annual_only():
    assert calculate_tax(v("osno_company",expenses=0,loss=90000))["income_tax"]==12500
    with pytest.raises(ValueError):v("usn_income_expense",loss=1)
    assert calculate_tax(v("usn_income_expense",expenses=0,loss=90000,month="2026-12"))["income_tax"]==1500

def test_vat_credit_not_negative_income_tax_or_refund_payment():
    x=calculate_tax(v("usn_income",vat_mode="general22",vat_output=22000,vat_input=25000))
    assert x["vat_to_pay"]==0 and x["vat_credit"]==3000 and x["estimate"]==6000
    with pytest.raises(ValueError):v("usn_income",vat_mode="special5",vat_input=1)
    with pytest.raises(ValueError):v("ausn_income",vat_mode="general22")
    with pytest.raises(ValueError):v("osno_company",vat_mode="special5")
    with pytest.raises(ValueError):v("usn_income",vat_mode="general20")

def test_missing_and_explicit_zero():
    assert "expenses" in calculate_tax(v("usn_income_expense",expenses=None))["missing"]
    assert calculate_tax(v("usn_income",expenses=None))["estimate"]==6000
    assert "vat_output" in calculate_tax(v("usn_income",vat_mode="special5",vat_output=None))["missing"]
    assert calculate_tax(v("ausn_income",received=0))["estimate"]==0

def test_reject_wrong_period_year_and_rates():
    for kw in [dict(period_mode="month"),dict(rate=7),dict(month="2024-09")]:
        with pytest.raises(ValueError):v("usn_income",**kw)
    with pytest.raises(ValueError):v("ausn_income",rate=6)

def test_company_insurance_capped_even_without_staff_flag():
    assert calculate_tax(v("usn_income",insurance=9000,taxpayer_type="company",has_staff=False))["income_tax"]==3000
    with pytest.raises(ValueError):v("osno_company",taxpayer_type="ip")

def test_progressive_bracket_edges_and_general_vat_2025():
    assert calculate_tax(v("osno_ip",received=2400100,expenses=0))["income_tax"]==312015
    assert calculate_tax(v("usn_income",month="2025-09",vat_mode="general20",vat_output=20000,vat_input=10000))["estimate"]==16000
    assert calculate_tax(v("usn_income_expense",expenses=100000,month="2026-12",rate=0))["estimate"]==0

def test_usn_interim_does_not_require_disallowed_loss():
    assert calculate_tax(v("usn_income_expense",loss=None))["estimate"]==1500

def test_rate_precision_and_missing_expenses_cannot_be_assumed_zero():
    with pytest.raises(ValueError):v("usn_income",rate=5.001)
    assert calculate_tax(v("osno_company",expenses=None))["estimate"] is None
    assert calculate_tax(v("ausn_income_expense",loss=None))["estimate"] is None
