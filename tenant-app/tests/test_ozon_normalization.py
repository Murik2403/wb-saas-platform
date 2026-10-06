import copy
from decimal import Decimal

import pytest

from db.ozon_normalization import preview_snapshot


def money(amount, currency="RUB"):
    return {"amount": str(amount), "currency": currency}


def snapshot(rows):
    return {"start_date": "2026-09-01", "end_date": "2026-09-30", "accruals": rows,
            "types": [{"id": 1, "name": "Service"}], "finance_postings": []}


def fee(amount="-0.10"):
    return {"accrual_id": 1, "date": "2026-09-01", "accrued_category": "NON_ITEM",
            "total_amount": money(amount), "non_item_fee": {"type_id": 1, "accrued": money(amount)}}


def document(event="sale-1", kind="sale", total="80.00", sku="123", qty=1, original=""):
    return {"namespace": "main", "external_event_id": event, "kind": kind,
            "accrued_at": "2026-09-01T10:00:00+00:00", "movement_at": "2026-08-31T10:00:00+00:00",
            "posting_ref": "posting-one", "sku": sku, "quantity": qty, "return_of": original,
            "revenue_rub": "100.00" if kind == "sale" else "-100.00",
            "commission_rub": "-20.00" if kind == "sale" else "20.00", "logistics_rub": "0.00",
            "other_rub": "0.00", "source_total_rub": total, "currency": "RUB", "document_ref": "paper"}


def posting(total="80.00"):
    return {"accrual_id": 2, "date": "2026-09-01", "accrued_category": "POSTING",
            "total_amount": money(total), "unit_number": "not-a-posting-identity", "posting": {"products": [{"sku": 123}]}}


def test_signed_fee_decimal_and_occurrences_are_never_double_deduplicated():
    raw = snapshot([fee(), fee(), fee("0.20")])
    before = copy.deepcopy(raw)
    result = preview_snapshot(raw)
    assert result["normalized_total_rub"] == "0.00"
    assert len({r["source_occurrence_key"] for r in result["rows"]}) == 3
    assert raw == before and result == preview_snapshot(raw)
    assert result["financial_normalization_verified"] is False
    assert result["marketplace_source_complete"] is False


def test_correction_changes_fingerprint_and_does_not_add_old_snapshot():
    first = preview_snapshot(snapshot([fee("-10.00")]))
    corrected = preview_snapshot(snapshot([fee("-12.00")]))
    assert first["source_fingerprint"] != corrected["source_fingerprint"]
    assert corrected["normalized_total_rub"] == "-12.00"


@pytest.mark.parametrize("change", [{"total_amount": money("1", "USD")}, {"non_item_fee": {"type_id": 77, "accrued": money("-0.10")}}, {"accrued_category": "NEW"}])
def test_unknown_currency_type_or_category_is_not_zero(change):
    result = preview_snapshot(snapshot([{**fee(), **change}]))
    assert result["normalized_total_rub"] is None
    assert result["rows"][0]["eligible"] is False
    assert result["rows"][0]["blockers"]


def test_empty_and_unsupported_posting_do_not_infer_movement_or_profit():
    assert preview_snapshot(snapshot([]))["normalized_total_rub"] is None
    result = preview_snapshot(snapshot([posting()]))
    assert result["rows"][0]["eligible"] is False
    assert result["result_after_fifo_rub"] is None
    assert result["rows"][0]["quantity"] is None


def test_documented_posting_bridge_uses_existing_fifo_without_inventing_time():
    raw = snapshot([posting()])
    row = preview_snapshot(raw)["rows"][0]
    doc = document()
    lot = {"source_key": "lot", "namespace": "main", "sku": "123", "received_at": "2026-08-01T00:00:00+00:00", "quantity": 1, "unit_cost_rub": "30.00"}
    result = preview_snapshot(raw, events=[doc], lots=[lot], links={row["source_occurrence_key"]: ["sale-1"]})
    assert result["normalized_total_rub"] == "80.00"
    assert result["known_cogs_rub"] == "30.00"
    assert result["result_after_fifo_rub"] == "50.00"
    assert result["rows"][0]["movement_basis"] == "existing_documented_movement_timestamp"
    assert result["rows"][0]["quantity"] == 1
    assert result["rows"][0]["source"] == "explicit_document_link"


def test_multi_sku_document_link_reconciles_aggregate_without_copying_total_to_each():
    raw = snapshot([posting("160.00")])
    raw["accruals"][0]["posting"]["products"].append({"sku": 456})
    row = preview_snapshot(raw)["rows"][0]
    events = [document("one"), document("two", sku="456")]
    result = preview_snapshot(raw, events=events, links={row["source_occurrence_key"]: ["one", "two"]})
    assert result["normalized_total_rub"] == "160.00"
    assert result["rows"][0]["quantity"] == 2
    assert result["result_after_fifo_rub"] is None
    assert result["unknown_cost_units"] == 2


def test_component_mismatch_and_reused_document_event_fail_closed():
    raw = snapshot([posting(), posting()])
    rows = preview_snapshot(raw)["rows"]
    links = {r["source_occurrence_key"]: ["sale-1"] for r in rows}
    result = preview_snapshot(raw, events=[document()], links=links)
    assert all(not r["eligible"] for r in result["rows"])
    bad = document(); bad["source_total_rub"] = "79.99"
    result = preview_snapshot(snapshot([posting()]), events=[bad], links={rows[0]["source_occurrence_key"]: ["sale-1"]})
    assert result["normalized_total_rub"] is None


def test_unknown_original_return_is_not_eligible_for_fifo_bridge():
    raw = snapshot([posting("-80.00")])
    row = preview_snapshot(raw)["rows"][0]
    doc = document("return", "return", "-80.00", original="missing")
    result = preview_snapshot(raw, events=[doc], links={row["source_occurrence_key"]: ["return"]})
    assert result["rows"][0]["eligible"] is False
    assert result["result_after_fifo_rub"] is None


def test_signed_return_uses_original_documented_cogs():
    sale, returned = document(), document("return", "return", "-80.00", original="sale-1")
    returned["movement_at"] = "2026-09-01T08:00:00+00:00"
    lot = {"source_key": "lot", "namespace": "main", "sku": "123", "received_at": "2026-08-01T00:00:00+00:00", "quantity": 1, "unit_cost_rub": "30.00"}
    raw = snapshot([posting(), {**posting("-80.00"), "accrual_id": 3}])
    rows = preview_snapshot(raw)["rows"]
    result = preview_snapshot(raw, events=[sale, returned], lots=[lot], links={rows[0]["source_occurrence_key"]: ["sale-1"], rows[1]["source_occurrence_key"]: ["return"]})
    assert result["normalized_total_rub"] == "0.00"
    assert result["known_cogs_rub"] == "0.00"
    assert result["result_after_fifo_rub"] == "0.00"
    assert result["rows"][1]["known_cogs_rub"] == "-30.00"


from .base import DbTestCase
from .test_ozon_pnl import csv_rows
from db.ozon_accounting import import_snapshot
from db.ozon_normalization import preview, confirm_link
from db.ozon_pnl import COLUMNS, confirm_import, preview_import
from db.core import connect
from unittest.mock import patch, Mock
import db.ozon_normalization as normalization


class NormalizationPersistenceTests(DbTestCase):
    def setUp(self):
        super().setUp()
        import_snapshot({**snapshot([posting()]), "products": [], "stocks": [], "fbo": [], "fbs": [], "returns": []})
        doc = document()
        row = {key: str(doc.get(key, "")) for key in COLUMNS}
        source = csv_rows([row])
        prepared = preview_import(source, "main")
        confirm_import(source, "main", prepared["fingerprint"], "owner")

    def link(self, prepared=None):
        prepared = prepared or preview("2026-09-01", "2026-09-30")
        return confirm_link("2026-09-01", "2026-09-30", "main", prepared["fingerprint"], prepared["rows"][0]["source_occurrence_key"], ["sale-1"], "owner")

    def test_confirmation_replay_is_idempotent_and_never_writes_manual_or_wb(self):
        prepared = preview("2026-09-01", "2026-09-30")
        with connect() as conn:
            before = {table: [tuple(r) for r in conn.execute("SELECT * FROM " + table)] for table in ("stocks", "sales", "ozon_document_events", "ozon_document_cost_lots")}
        self.assertFalse(self.link(prepared)["duplicate"])
        self.assertTrue(self.link(prepared)["duplicate"])
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ozon_normalization_links").fetchone()[0], 1)
            for table, rows in before.items():
                self.assertEqual([tuple(r) for r in conn.execute("SELECT * FROM " + table)], rows)

    def test_source_correction_invalidates_old_links_and_stale_confirmation(self):
        prepared = preview("2026-09-01", "2026-09-30")
        self.link(prepared)
        import_snapshot({**snapshot([posting("79.00")]), "products": [], "stocks": [], "fbo": [], "fbs": [], "returns": []})
        self.assertIsNone(preview("2026-09-01", "2026-09-30")["normalized_total_rub"])
        with self.assertRaises(ValueError):
            self.link(prepared)
        with connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM ozon_normalization_links").fetchone()[0], 1)

    def test_document_change_invalidates_preview(self):
        prepared = preview("2026-09-01", "2026-09-30")
        row = {key: str(document("another").get(key, "")) for key in COLUMNS}
        source = csv_rows([row]); prior = preview_import(source, "main")
        confirm_import(source, "main", prior["fingerprint"], "owner")
        with self.assertRaises(ValueError):
            self.link(prepared)

    def test_newer_overlapping_snapshot_never_reuses_stale_wide_period(self):
        raw = snapshot([posting("79.00")]); raw["end_date"] = "2026-09-01"
        import_snapshot({**raw, "products": [], "stocks": [], "fbo": [], "fbs": [], "returns": []})
        with self.assertRaisesRegex(ValueError, "перекрывающей"):
            preview("2026-09-01", "2026-09-30")

    def test_foreign_namespace_is_filtered_in_sql_before_payload_decode(self):
        before = preview("2026-09-01", "2026-09-30")
        with connect() as conn:
            for table in ("ozon_document_events", "ozon_document_cost_lots"):
                conn.execute(f"INSERT INTO {table}(source_key,payload_json,actor,created_at) VALUES(?,?,?,?)", ('["foreign","foreign-id"]', 'invalid foreign JSON' * 10000, 'owner', '2026-09-01'))
        after = preview("2026-09-01", "2026-09-30")
        self.assertEqual(before["fingerprint"], after["fingerprint"])
        choices = normalization.documents("2026-09-01", "2026-09-30")
        self.assertEqual([row["external_event_id"] for row in choices["lines"]], ["sale-1"])

    def test_document_count_and_byte_caps_precede_json_decode(self):
        for limit, value in (("MAX_DOCUMENT_ROWS", 0), ("MAX_DOCUMENT_BYTES", 1)):
            with patch.object(normalization, limit, value), patch.object(normalization.json, "loads", Mock(side_effect=AssertionError("must not decode"))):
                with connect() as conn:
                    with self.assertRaisesRegex(ValueError, "безопасный объём"):
                        normalization._document_rows(conn, "ozon_document_events", "main")
        with patch.object(normalization, "MAX_DOCUMENT_BYTES", 1):
            with self.assertRaises(ValueError):
                normalization.documents("2026-09-01", "2026-09-30")

    def test_snapshot_size_cap_precedes_json_decode(self):
        with patch.object(normalization, "MAX_SNAPSHOT_BYTES", 1), patch.object(normalization.json, "loads", Mock(side_effect=AssertionError("must not decode"))):
            with self.assertRaisesRegex(ValueError, "Сохранённый источник"):
                preview("2026-09-01", "2026-09-30")

    def test_link_count_and_byte_caps_precede_link_json_decode(self):
        self.link()
        original_loads = normalization.json.loads
        def loads(encoded, *args, **kwargs):
            if encoded == '["sale-1"]':
                raise AssertionError("link payload must not be decoded")
            return original_loads(encoded, *args, **kwargs)
        for limit, value in (("MAX_LINK_ROWS", 0), ("MAX_LINK_BYTES", 1)):
            with patch.object(normalization, limit, value), patch.object(normalization.json, "loads", loads):
                with self.assertRaisesRegex(ValueError, "Связи документов"):
                    preview("2026-09-01", "2026-09-30")
