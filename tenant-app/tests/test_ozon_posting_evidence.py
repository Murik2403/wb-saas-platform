import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ozon_accounting_client import OzonAccountingClient, OzonAccountingError
from test_ozon_accounting import Session, fixture_snapshot
from db.ozon_posting_evidence import posting_evidence
from base import DbTestCase
from db.core import connect
from db.ozon_accounting import import_snapshot, workspace


def group(number="one"):
    return {"posting_number": number, "accruals": [{"accrual_date": "2026-09-01", "quantity": 2, "sku": 123,
        "type_id": 1, "accrued": {"amount": "-12.34", "currency": "RUB"},
        "seller_price": {"amount": "100", "currency": "RUB"}}]}


def test_finance_postings_reads_sequential_bounded_batches_preserving_rows():
    numbers = [f"posting-{i}" for i in range(201)]
    first = group(numbers[0])
    session = Session([{"posting_accruals": [first, first]}, {"posting_accruals": [group(numbers[-1])]}])
    client = OzonAccountingClient("not-real", "not-real", session=session)
    result = client.finance_postings(numbers + [numbers[0]])
    assert len(result) == 3
    assert len(session.calls) == 2
    assert session.calls[0][1] == {"posting_numbers": numbers[:200]}
    assert session.calls[1][1] == {"posting_numbers": numbers[200:]}
    assert all(url.endswith("/v1/finance/accrual/postings") for url, _ in session.calls)
    assert len(client.page_receipts) == 2


@pytest.mark.parametrize("payload", [{}, {"posting_accruals": None}, {"posting_accruals": ["broken"]},
    {"posting_accruals": [group("unrequested")]}, {"posting_accruals": [{"posting_number": "one", "accruals": None}]}])
def test_bad_or_unrequested_finance_response_fails_closed(payload):
    client = OzonAccountingClient("fake", "fake", session=Session([payload]))
    with pytest.raises(OzonAccountingError): client.finance_postings(["one"])


def test_source_quantity_does_not_become_sale_quantity_or_fifo_evidence():
    raw = group()
    raw["accruals"].append({**raw["accruals"][0], "quantity": -1})
    result = posting_evidence({"finance_postings": [raw, raw]})
    assert len(result["rows"]) == 4
    assert len({r["source_occurrence_key"] for r in result["rows"]}) == 4
    assert [r["quantity_text"] for r in result["rows"]] == ["2", "-1", "2", "-1"]
    assert result["movement_verified"] is False
    assert result["financial_normalization_verified"] is False
    assert result["ledger_confirmation_allowed"] is False
    assert result["return_linkage_verified"] is False
    assert result == posting_evidence({"finance_postings": [raw, raw]})


def test_missing_source_is_unknown_and_changed_version_not_deduplicated():
    assert posting_evidence({})["loaded"] is False
    before = posting_evidence({"finance_postings": [group()]})
    changed = group(); changed["accruals"][0]["quantity"] = 3
    after = posting_evidence({"finance_postings": [changed]})
    assert after["source_version"] != before["source_version"]
    assert after["rows"][0]["source_occurrence_key"] != before["rows"][0]["source_occurrence_key"]


@pytest.mark.parametrize("qty", [True, "2", 2.5, None])
def test_malformed_quantity_is_not_rendered_as_numeric_evidence(qty):
    source = group(); source["accruals"][0]["quantity"] = qty
    result = posting_evidence({"finance_postings": [source]})
    assert result["rows"][0]["quantity_text"] is None
    assert result["rows"][0]["reason"]


def test_finance_child_row_budget_and_empty_input():
    session = Session([])
    client = OzonAccountingClient("fake", "fake", session=session, max_rows=1)
    assert client.finance_postings([]) == [] and session.calls == []
    raw = group(); raw["accruals"] *= 2
    client.session = Session([{"posting_accruals": [raw]}])
    with pytest.raises(OzonAccountingError): client.finance_postings(["one"])


def test_snapshot_uses_order_posting_number_not_by_day_unit_or_sku(monkeypatch):
    source = group()
    client = OzonAccountingClient("fake", "fake", session=Session([
        {"accrual_types": [{"id": 1}]}, {"posting_accruals": [source]}]))
    def pages(path, *args, **kwargs):
        if path == "/v3/posting/fbo/list": return [{"posting_number": "one"}]
        if path == "/v4/posting/fbs/list": return [{"posting_number": "one"}]
        if path == "/v1/finance/accrual/by-day": return [{"unit_number": "not-a-posting-number"}]
        return []
    monkeypatch.setattr(client, "_pages", pages)
    snapshot = client.snapshot("2026-09-01", "2026-09-01")
    assert snapshot["finance_postings"] == [source]
    assert client.session.calls[-1][1] == {"posting_numbers": ["one"]}
    assert snapshot["finance_postings_scope"] == {"requested_number_count":1,"date_filter_supported":False,"missing_number_count":0,"normalization_verified":False}


def test_missing_response_never_certifies_coverage_and_nullable_fields_stay_unknown():
    source = group(); source["accruals"][0].update(quantity=None, seller_price=None, sku=None)
    result = posting_evidence({"finance_postings": [source]})
    assert result["rows"][0]["quantity_text"] is None
    assert result["rows"][0]["seller_price_rub"] is None
    assert result["rows"][0]["sku_text"] is None
    assert result["movement_verified"] is False
    assert OzonAccountingClient("fake", "fake", session=Session([{"posting_accruals":[]}])).finance_postings(["one"]) == []


class PostingEvidenceDbTests(DbTestCase):
    def test_immutable_source_versions_display_without_posting_or_wb_writes(self):
        import json
        snapshot = fixture_snapshot()
        snapshot["finance_postings"] = [group()]
        with connect() as conn:
            before_sales = [tuple(r) for r in conn.execute("SELECT * FROM sales")]
        import_snapshot(snapshot)
        first = workspace("2026-09-01", "2026-09-02")
        assert first["posting_accrual_evidence"]["rows"][0]["quantity_text"] == "2"
        assert first["profit_rub"] is None and first["financial_normalization_verified"] is False
        snapshot["finance_postings"][0]["accruals"][0]["quantity"] = 3
        import_snapshot(snapshot)
        with connect() as conn:
            sources = [json.loads(r[0]) for r in conn.execute("SELECT payload_json FROM ozon_raw_source_versions ORDER BY id")]
            assert [s["finance_postings"][0]["accruals"][0]["quantity"] for s in sources] == [2, 3]
            assert conn.execute("SELECT COUNT(*) FROM ozon_document_events").fetchone()[0] == 0
            assert [tuple(r) for r in conn.execute("SELECT * FROM sales")] == before_sales
            assert conn.execute("SELECT COUNT(*) FROM ozon_accrual_ledger").fetchone()[0] == 1
