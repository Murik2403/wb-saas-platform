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
        assert result["fee_preview"]["reconciled_total_rub"] == "-12.34"
        assert result["fee_preview"]["ledger_confirmation_allowed"] is False
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
        assert result["source_complete"] is False
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

    def test_missing_or_unrecognized_trade_detail_never_means_confirmed_zero_cost(self):
        for category in ("POSTING","ITEM"):
            for posting in (None,{},[],{"products":[]},{"products":"unknown"},{"products":["unknown"]}):
                row={"accrual_id":2,"date":"2026-09-01","total_amount":{"amount":"100","currency":"RUB"},"accrued_category":category,"posting":posting}
                import_snapshot(fixture_snapshot([row]))
                result=workspace("2026-09-01","2026-09-02")
                assert result["source_complete"] is False
                assert result["accounting_complete"] is False
                assert result["profit_rub"] is None
                assert result["estimate_after_known_cogs_rub"] is None
                assert result["blockers"]


class Response:
    status_code = 200
    def __init__(self, body): self.body = body
    def json(self): return self.body
    def iter_content(self,chunk_size):
        import json
        yield json.dumps(self.body).encode()
    def close(self): pass
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


@pytest.mark.parametrize("cursor", [False, 0, None, [], {}, "x"*4097])
def test_wrong_cursor_type_cannot_certify_terminal_page(cursor):
    client=OzonAccountingClient("fake","fake",session=Session([{"accruals":[],"last_id":cursor}]))
    with pytest.raises(OzonAccountingError):
        client._pages("/v1/finance/accrual/by-day",{},rows="accruals",cursor="last_id")


class RawResponse:
    def __init__(self, raw, status=200): self.raw=raw;self.status_code=status;self.closed=False
    def iter_content(self, chunk_size):
        for i in range(0,len(self.raw),chunk_size): yield self.raw[i:i+chunk_size]
    def close(self): self.closed=True


class RawSession:
    def __init__(self,response): self.response=response;self.kwargs=None
    def post(self,url,**kwargs): self.kwargs=kwargs;return self.response


def test_stream_preserves_decimal_lexeme_and_never_follows_credential_redirects():
    response=RawResponse(b'{"amount":123456789.123456789,"count":2}')
    session=RawSession(response);client=OzonAccountingClient("fake","fake",session=session)
    result=client._post("/v1/finance/accrual/by-day",{})
    assert result=={"amount":"123456789.123456789","count":2}
    assert session.kwargs["allow_redirects"] is False and session.kwargs["stream"] is True
    assert response.closed and len(client.page_receipts)==1
    redirect=RawResponse(b'{}',302);client.session=RawSession(redirect)
    with pytest.raises(OzonAccountingError,match="HTTP 302"): client._post("/test",{})
    assert redirect.closed and len(client.page_receipts)==1


@pytest.mark.parametrize("raw", [b'{"money":NaN}', b'[]', b'\xff', b'{broken', b' '* (2*1024*1024+1), b'['*2000+b']'*2000], ids=['nan','array','utf8','syntax','oversize','depth'])
def test_malformed_or_oversize_stream_closes_without_receipt(raw):
    response=RawResponse(raw);client=OzonAccountingClient("fake","fake",session=RawSession(response))
    with pytest.raises(OzonAccountingError): client._post("/test",{})
    assert response.closed and client.page_receipts==[]


def test_aggregate_stream_budget_cannot_be_bypassed_by_small_pages():
    response=RawResponse(b'{}');client=OzonAccountingClient("fake","fake",session=RawSession(response))
    client.byte_budget=1
    with pytest.raises(OzonAccountingError): client._post("/test",{})
    assert response.closed and not client.page_receipts


def empty_client_snapshot():
    pages=[{"result":{"items":[],"last_id":""}}, {"items":[],"cursor":""},
           {"postings":[],"has_next":False}, {"postings":[],"has_next":False},
           {"returns":[],"has_next":False}, {"accrual_types":[]},
           {"accruals":[],"last_id":""}, {"accruals":[],"last_id":""}]
    return OzonAccountingClient("fake","fake",session=Session(pages)).snapshot("2026-09-01","2026-09-02")


class OzonCollectionQualityTests(DbTestCase):
    def test_completed_empty_reads_are_not_confirmed_zero_profit(self):
        snapshot=empty_client_snapshot();import_snapshot(snapshot)
        result=workspace("2026-09-01","2026-09-02")
        assert result["source_collection_complete"] is True
        assert result["source_complete"] is False and result["accounting_complete"] is False
        assert result["financial_normalization_verified"] is False
        assert result["net_accrual_rub"] is None and result["profit_rub"] is None
        assert result["estimate_after_known_cogs_rub"] is None

    def test_legacy_or_incomplete_day_proof_does_not_claim_collection(self):
        for mutation in (lambda s:s.pop("collection_provenance"),
                         lambda s:s["collection_provenance"].update(completed_days=["2026-09-01"]),
                         lambda s:s["collection_provenance"].update(schema_version=True),
                         lambda s:s["collection_provenance"].update(financial_normalization_verified=True)):
            snapshot=empty_client_snapshot();mutation(snapshot);import_snapshot(snapshot)
            assert workspace("2026-09-01","2026-09-02")["source_collection_complete"] is False

    def test_partial_remote_failure_preserves_existing_window(self):
        import_snapshot(fixture_snapshot())
        pages=[{"result":{"items":[],"last_id":""}}, {"items":[],"cursor":""},
               {"postings":[],"has_next":False}, {"postings":[],"has_next":False},
               {"returns":[],"has_next":False}, {"accrual_types":[]},
               {"accruals":[],"last_id":""}, {"accruals":[],"last_id":False}]
        with pytest.raises(OzonAccountingError):
            candidate=OzonAccountingClient("fake","fake",session=Session(pages)).snapshot("2026-09-01","2026-09-02")
            import_snapshot(candidate)
        assert workspace("2026-09-01","2026-09-02")["net_accrual_rub"]=="-12.34"


def test_deadline_and_network_failure_are_sanitized_and_never_get_receipts(monkeypatch):
    import requests
    import ozon_accounting_client as module
    response=RawResponse(b'{}');client=OzonAccountingClient("fake","secret-marker",session=RawSession(response))
    client.deadline=0
    with pytest.raises(OzonAccountingError): client._post("/test",{})
    assert client.session.kwargs is None
    class BrokenSession:
        def post(self,*args,**kwargs): raise requests.RequestException("secret-marker untrusted response")
    client=OzonAccountingClient("fake","secret-marker",session=BrokenSession())
    with pytest.raises(OzonAccountingError) as error: client._post("/test",{})
    assert "secret-marker" not in str(error.value) and not client.page_receipts
    client=OzonAccountingClient("fake","fake",session=RawSession(response))
    ticks=iter([1,301]);client.deadline=300
    monkeypatch.setattr(module.time,"monotonic",lambda:next(ticks))
    with pytest.raises(OzonAccountingError): client._post("/test",{})
    assert response.closed and not client.page_receipts
