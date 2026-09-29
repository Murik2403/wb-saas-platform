import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
import pytest
from base import DbTestCase
from db.core import connect
from db.ozon_accounting import import_snapshot, workspace, save_cost_mapping
from ozon_accounting_client import OzonAccountingClient, OzonAccountingError, checked_period


def fixture_snapshot(rows=None):
    return {"start_date":"2026-09-01","end_date":"2026-09-02","products":[{"product_id":1,"sku":123,"offer_id":"offer"}],"stocks":[],"fbo":[],"fbs":[],"returns":[],"types":[{"id":1,"name":"Acquiring","description":"Эквайринг"}],"accruals": rows or [{"accrual_id":1,"date":"2026-09-01","total_amount":{"amount":"-12.34","currency":"RUB"},"accrued_category":"NON_ITEM","non_item_fee":{"type_id":1,"accrued":{"amount":"-12.34","currency":"RUB"}}}]}


class OzonLedgerTests(DbTestCase):
    def test_import_is_idempotent_decimal_exact_and_never_changes_wb_tables(self):
        snapshot = fixture_snapshot()
        with connect() as conn:
            before = [tuple(row) for row in conn.execute("SELECT * FROM sales")]
        import_snapshot(snapshot); import_snapshot(snapshot)
        result = workspace("2026-09-01","2026-09-02")
        assert result["net_accrual_rub"] == "-12.34"
        assert result["fees"][0]["name"] == "Эквайринг"
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM ozon_accrual_ledger").fetchone()[0] == 1
            assert [tuple(row) for row in conn.execute("SELECT * FROM sales")] == before

    def test_identical_rows_preserved_and_reimport_never_accumulates(self):
        snapshot = fixture_snapshot(); snapshot["accruals"] *= 2
        result = import_snapshot(snapshot)
        assert result["duplicates"] == 0
        assert result["identical_row_occurrences"] == 1
        import_snapshot(snapshot)
        report = workspace("2026-09-01","2026-09-02")
        assert report["net_accrual_rub"] == "-24.68"
        assert report["accounting_complete"] is False
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM ozon_accrual_ledger").fetchone()[0] == 2

    def test_reused_accrual_id_distinct_rows_and_days_are_not_dropped(self):
        snapshot = fixture_snapshot()
        other = {**snapshot["accruals"][0], "total_amount":{"amount":"999","currency":"RUB"}}
        next_day = {**other, "date":"2026-09-02"}
        import_snapshot({**snapshot,"accruals":snapshot["accruals"]+[other,next_day]})
        assert workspace("2026-09-01","2026-09-02")["net_accrual_rub"] == "1985.66"
        import_snapshot({**snapshot,"start_date":"2026-09-02","accruals":[next_day]})
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM ozon_accrual_ledger").fetchone()[0] == 3

    def test_invalid_money_leaves_previous_report(self):
        snapshot = fixture_snapshot(); import_snapshot(snapshot)
        invalid = {**snapshot["accruals"][0],"total_amount":{"amount":"NaN","currency":"RUB"}}
        with pytest.raises(ValueError): import_snapshot({**snapshot,"accruals":[invalid]})
        assert workspace("2026-09-01","2026-09-02")["net_accrual_rub"] == "-12.34"

    def test_unknown_fees_and_currency_block_complete_accounting(self):
        snapshot = fixture_snapshot()
        snapshot["accruals"][0]["non_item_fee"]["type_id"] = 999
        snapshot["accruals"][0]["total_amount"]["currency"] = "CNY"
        import_snapshot(snapshot)
        result = workspace("2026-09-01","2026-09-02")
        assert result["source_complete"] is True
        assert result["accounting_complete"] is False
        assert result["profit_rub"] is None
        assert any("999" in blocker for blocker in result["blockers"])

    def test_missing_cost_stays_unknown_and_manual_cost_is_estimated_not_fifo(self):
        posting = {"accrual_id":2,"date":"2026-09-01","total_amount":{"amount":"100","currency":"RUB"},"accrued_category":"POSTING","posting":{"products":[{"sku":123,"quantity":2,"commission":{"sale_amount":{"amount":"120","currency":"RUB"}}}]}}
        import_snapshot(fixture_snapshot([posting]))
        unknown = workspace("2026-09-01","2026-09-02")
        assert unknown["known_cost_units"] == 0
        assert unknown["estimate_after_known_cogs_rub"] is None
        save_cost_mapping("123","manual","12.50")
        result = workspace("2026-09-01","2026-09-02")
        assert result["estimated_cogs_rub"] == "25.00"
        assert result["exact_fifo_units"] == 0
        assert result["profit_rub"] is None
        assert not result["accounting_complete"]

    def test_cost_mapping_validation_and_history_noop(self):
        with pytest.raises(ValueError): save_cost_mapping("123","manual","Infinity")
        with pytest.raises(ValueError): save_cost_mapping("123","wb_estimate",nm_id=999)
        save_cost_mapping("123","manual","0"); save_cost_mapping("123","manual","0")
        with connect() as conn:
            assert conn.execute("SELECT COUNT(*) FROM ozon_cost_mapping_history").fetchone()[0] == 1


class Response:
    status_code = 200
    def __init__(self, body): self.body = body
    def json(self): return self.body
class Session:
    def __init__(self, pages): self.pages = list(pages); self.calls = []
    def post(self, url, **kwargs):
        self.calls.append((url,kwargs["json"]))
        return Response(self.pages.pop(0))


def test_pagination_repeated_cursor_is_not_reported_complete():
    client = OzonAccountingClient("not-real","not-real",session=Session([{"items":[{}],"cursor":"a"},{"items":[{}],"cursor":"a"}]))
    with pytest.raises(OzonAccountingError,match="не продвигается"):
        client._pages("/v4/product/info/stocks",{},rows="items",cursor="cursor")


def test_missing_cursor_and_page_cap_fail_closed():
    client = OzonAccountingClient("not-real","not-real",session=Session([{"items":[{}]}]))
    with pytest.raises(OzonAccountingError): client._pages("/v4/product/info/stocks",{},rows="items",cursor="cursor")
    client = OzonAccountingClient("not-real","not-real",session=Session([{"items":[{}],"cursor":"next"}]),max_pages=1)
    with pytest.raises(OzonAccountingError,match="предел страниц"): client._pages("/v4/product/info/stocks",{},rows="items",cursor="cursor")


def test_numeric_return_cursor_uses_last_row_identifier():
    session = Session([{"returns":[{"id":42}],"has_next":True},{"returns":[],"has_next":False}])
    client = OzonAccountingClient("not-real","not-real",session=session)
    assert len(client._pages("/v1/returns/list",{},rows="returns",cursor="last_id",has_next=True,numeric=True)) == 1
    assert session.calls[1][1]["last_id"] == 42


def test_period_is_bounded_and_reversed_period_rejected():
    with pytest.raises(OzonAccountingError): checked_period("2026-08-01","2026-09-30")
    with pytest.raises(OzonAccountingError): checked_period("2026-09-02","2026-09-01")
