import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from db import core
from db.cashflow import create_entry, change_status, cash_report

@pytest.fixture(autouse=True)
def database(monkeypatch,tmp_path):
    monkeypatch.setattr(core,"DB_PATH",tmp_path/"cash.sqlite")
    core.init_db()

def entry(key="request-001",**kwargs):
    return {"request_key":key,"kind":"movement","direction":"inflow","status":"planned",
      "planned_date":"2026-09-10","actual_date":None,"amount":"100","exchange_rate":"1",
      "currency":"RUB","category":"other","reference":"",**kwargs}

def test_fx_sign_rounding_actual_and_forecast_reconciliation():
    create_entry(entry("opening-001",kind="opening",category="opening",status="actual",planned_date="2026-09-01",actual_date="2026-09-01",amount="1000"))
    create_entry(entry("historic-001",status="actual",actual_date="2026-08-31",amount="90000"))
    create_entry(entry("actual-001",status="actual",actual_date="2026-09-02",amount="200"))
    expense=create_entry(entry("expense-001",direction="outflow",amount="37.5",currency="CNY",exchange_rate="13"))
    result=cash_report("2026-09-01","2026-09-19")
    assert result["opening_balance_minor"]==100000
    assert result["actual_closing_minor"]==120000
    assert result["forecast_closing_minor"]==71250
    assert expense["amount_minor"]==48750
    change_status(expense["id"],"actual","2026-09-15")
    after=cash_report("2026-09-01","2026-09-19")
    assert after["actual_closing_minor"]==after["forecast_closing_minor"]==71250
    assert after["actual_inflow_minor"]-after["actual_outflow_minor"]+after["opening_balance_minor"]==71250
    assert len(after["events"])==5
    assert after["daily"][-1]["forecast_balance_minor"]==71250

def test_partial_window_overdue_future_actual_and_cancelled():
    create_entry(entry("before-001",status="actual",actual_date="2026-09-02",amount="500"))
    overdue=create_entry(entry("overdue-001",direction="outflow",planned_date="2026-09-03",amount="50"))
    create_entry(entry("future-001",planned_date="2026-10-01",amount="9000"))
    result=cash_report("2026-09-10","2026-09-19")
    assert result["opening_balance_minor"]==50000 and not result["opening_confirmed"]
    assert result["forecast_closing_minor"]==45000
    assert result["overdue_planned_minor"]==-5000
    change_status(overdue["id"],"cancelled")
    assert cash_report("2026-09-10","2026-09-19")["forecast_closing_minor"]==50000
    assert next(r for r in cash_report("2026-09-10","2026-09-19")["items"] if r["id"]==overdue["id"])["status"]=="cancelled"
    with pytest.raises(ValueError):change_status(overdue["id"],"actual","2026-09-11")

def test_idempotence_payload_conflict_and_single_opening_anchor():
    first=create_entry(entry())
    assert create_entry(entry())["id"]==first["id"]
    with pytest.raises(ValueError):create_entry(entry(amount="101"))
    opening=create_entry(entry("opening-001",kind="opening",category="opening",status="actual",actual_date="2026-09-10"))
    with pytest.raises(ValueError):create_entry(entry("opening-002",kind="opening",category="opening",status="actual",actual_date="2026-09-10"))
    change_status(opening["id"],"cancelled")
    create_entry(entry("opening-002",kind="opening",category="opening",status="actual",actual_date="2026-09-10"))
    assert cash_report("2026-09-01","2026-09-19")["period_before_opening"]

def test_actual_confirmation_retry_and_changed_date_rejected():
    item=create_entry(entry())
    change_status(item["id"],"actual","2026-09-11")
    change_status(item["id"],"actual","2026-09-11")
    with pytest.raises(ValueError):change_status(item["id"],"actual","2026-09-12")
    assert len(cash_report("2026-09-01","2026-09-19")["events"])==2

def test_opening_cash_does_not_swallow_overdue_unpaid_obligation():
    create_entry(entry("opening-001",kind="opening",category="opening",status="actual",actual_date="2026-09-10",amount="1000"))
    create_entry(entry("overdue-001",direction="outflow",planned_date="2026-09-02",amount="50"))
    assert cash_report("2026-09-10","2026-09-19")["forecast_closing_minor"]==95000

@pytest.mark.parametrize("changes",[{"amount":"NaN"},{"amount":"-1"},{"exchange_rate":"0"},{"exchange_rate":"Infinity"},{"status":"actual"},{"currency":"RUB","exchange_rate":"13"},{"amount":"0"},{"category":"opening"}])
def test_invalid_money_and_semantics_rejected(changes):
    with pytest.raises((ValueError,ArithmeticError)):create_entry(entry(**changes))

def test_event_failure_rolls_back_status_and_creation_and_history_immutable():
    item=create_entry(entry())
    with core.connect() as conn:
        conn.execute("CREATE TRIGGER fail_cash_event BEFORE INSERT ON cash_entry_events BEGIN SELECT RAISE(ABORT,'fixture'); END")
    with pytest.raises(sqlite3.IntegrityError):change_status(item["id"],"actual","2026-09-12")
    with pytest.raises(sqlite3.IntegrityError):create_entry(entry("different-001"))
    result=cash_report("2026-09-01","2026-09-19")
    assert len(result["items"])==1 and result["items"][0]["status"]=="planned"
    with core.connect() as conn:
        with pytest.raises(sqlite3.IntegrityError):conn.execute("DELETE FROM cash_entry_events")

def test_concurrent_retry_is_one_entry_and_one_event():
    # Initialize before racing: serialized writes then exercise UNIQUE+BEGIN IMMEDIATE.
    cash_report("2026-09-01","2026-09-19")
    with ThreadPoolExecutor(max_workers=4) as pool:
        ids=list(pool.map(lambda _: create_entry(entry())["id"],range(8)))
    assert len(set(ids))==1
    report=cash_report("2026-09-01","2026-09-19")
    assert len(report["items"])==len(report["events"])==1

def test_accrual_sales_and_store_expenses_never_appear_as_cash():
    with core.connect() as conn:
        conn.execute("INSERT INTO sales(id,sale_date,for_pay,raw_json) VALUES('sale-1','2026-09-10',100000,'{}')")
    report=cash_report("2026-09-01","2026-09-19")
    assert report["actual_inflow_minor"]==report["actual_closing_minor"]==0
    assert report["items"]==[]
