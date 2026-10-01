import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from db.ozon_fee_preview import fee_preview


def row(category="NON_ITEM"):
    fee = {"type_id": 1, "accrued": {"amount": "-0.10", "currency": "RUB"}}
    result = {"date": "2026-09-01", "accrual_id": 7, "unit_number": "service", "accrued_category": category,
              "total_amount": {"amount": "-0.10", "currency": "RUB"}}
    result.update({"NON_ITEM": {"non_item_fee": fee}, "ITEM": {"item_fees": {"fees": [{"sku": 123, "fees": [fee]}]}},
                   "CONTAINER_FEES": {"container_fees": {"fees": [fee]}}}.get(category, {}))
    return result


def preview(rows):
    return fee_preview(rows, [{"id": 1, "description": "Fee"}])


@pytest.mark.parametrize("category", ["NON_ITEM", "ITEM", "CONTAINER_FEES"])
def test_supported_category_preserves_sign_and_source(category):
    source = row(category)
    original = copy.deepcopy(source)
    result = preview([source])
    assert source == original
    assert result["rows"][0]["component_sum_rub"] == "-0.10"
    assert result["rows"][0]["components"][0]["amount_rub"] == "-0.10"
    assert result["rows"][0]["reconciled"] is True
    assert result["financial_normalization_verified"] is False
    assert result["marketplace_source_complete"] is False
    assert result["ledger_confirmation_allowed"] is False


def test_duplicate_ids_and_identical_rows_retained_and_deterministic():
    sources = [row(), row(), {**row(), "unit_number": "another"}]
    result = preview(sources)
    assert result == preview(sources)
    assert len({r["source_occurrence_key"] for r in result["rows"]}) == 3
    assert result["reconciled_total_rub"] == "-0.30"


@pytest.mark.parametrize("change", [
    {"accrued_category": "POSTING"}, {"accrued_category": "UNSPECIFIED"},
    {"total_amount": {"amount": "-1", "currency": "RUB"}},
    {"total_amount": {"amount": "-0.10", "currency": "CNY"}},
    {"posting": {"products": []}},
    {"non_item_fee": {"type_id": 999, "accrued": {"amount": "-0.10", "currency": "RUB"}}},
    {"non_item_fee": {"type_id": 1, "accrued": {"amount": "NaN", "currency": "RUB"}}},
    {"non_item_fee": {"type_id": 1, "accrued": {"amount": 0.1, "currency": "RUB"}}},
    {"non_item_fee": {"type_id": 1, "accrued": {"amount": "-0.101", "currency": "RUB"}}},
])
def test_unsupported_or_unreconciled_never_contributes(change):
    result = preview([{**row(), **change}, row()])
    assert result["rows"][0]["reconciled"] is False
    assert result["rows"][0]["components"] == []
    assert result["reconciled_total_rub"] == "-0.10"
    assert result["preview_all_rows_reconciled"] is False


def test_empty_is_not_zero_profit_or_verified():
    result = preview([])
    assert result["reconciled_total_rub"] is None
    assert result["preview_all_rows_reconciled"] is False


def test_correction_creates_new_source_version_without_merging_operations():
    before = preview([row()])
    changed = row()
    changed["total_amount"]["amount"] = "0.20"
    changed["non_item_fee"]["accrued"]["amount"] = "0.20"
    after = preview([changed])
    assert before["source_version"] != after["source_version"]
    assert before["rows"][0]["source_occurrence_key"] != after["rows"][0]["source_occurrence_key"]
    assert after["reconciled_total_rub"] == "0.20"


def test_multiple_item_components_reconcile_once_and_keep_sku_paths():
    source = row("ITEM")
    source["item_fees"]["fees"] *= 2
    source["total_amount"]["amount"] = "-0.20"
    result = preview([source])["rows"][0]
    assert result["reconciled"] is True
    assert len({c["source_path"] for c in result["components"]}) == 2
    assert [c["sku"] for c in result["components"]] == ["123", "123"]


def test_ambiguous_dictionary_and_unknown_component_fields_fail_closed():
    result = fee_preview([row()], [{"id": 1}, {"id": 1}])
    assert result["rows"][0]["reconciled"] is False
    source = row()
    source["non_item_fee"]["unknown_money"] = {"amount": "2", "currency": "RUB"}
    assert preview([source])["rows"][0]["reconciled"] is False
