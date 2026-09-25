import csv
import io
import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from finance_csv import build_finance_csv
from production_permissions import write_allowed


def test_csv_roundtrip_unicode_decimals_quotes_and_formula_protection():
    original = pd.DataFrame([{"Артикул": '=HYPERLINK("bad")', "Прибыль": -376.50, "Описание": 'ткань;"синяя"\nстрока', "Пусто": None}])
    result = build_finance_csv(original)
    assert result.startswith(b"\xef\xbb\xbf")
    rows = list(csv.DictReader(io.StringIO(result.decode("utf-8-sig")), delimiter=";"))
    assert rows[0]["Артикул"].startswith("'=")
    assert float(rows[0]["Прибыль"]) == -376.50
    assert rows[0]["Описание"] == original.iloc[0]["Описание"]
    assert rows[0]["Пусто"] == ""


@pytest.mark.parametrize("role", ["viewer", "technologist", "cutter", "seamstress", "warehouse"])
def test_production_roles_cannot_change_credentials_or_prices(role):
    assert not write_allowed(role, "POST", "/api/v1/settings/wb-token")
    assert not write_allowed(role, "POST", "/api/v1/products/prices/upload")
    assert write_allowed(role, "GET", "/api/v1/manufacturing-costs")


@pytest.mark.parametrize("path", ["/api/v1/settings/wb-token", "/api/v1/settings/ozon"])
@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_marketplace_credentials_are_owner_only(path, method):
    assert not write_allowed("manager", method, path)
    assert write_allowed("owner", method, path)


def test_role_permissions_are_specific_to_operation():
    assert write_allowed("technologist", "POST", "/api/v1/manufacturing-costs/boms")
    assert not write_allowed("seamstress", "POST", "/api/v1/manufacturing-costs/boms")
    assert write_allowed("seamstress", "POST", "/api/v1/manufacturing-costs/piecework")
    assert write_allowed("cutter", "POST", "/api/v1/production-workspace/wip/12/complete")
    assert not write_allowed("cutter", "POST", "/api/v1/production-workspace/wip/12/reverse")
    assert not write_allowed("unknown", "GET", "/api/v1/manufacturing-costs")


def test_integration_reader_is_limited_to_three_read_routes():
    assert write_allowed("integration_reader", "GET", "/api/v1/integration/products")
    assert not write_allowed("integration_reader", "POST", "/api/v1/integration/products")
    assert not write_allowed("integration_reader", "GET", "/api/v1/settings/backups")
    assert not write_allowed("integration_reader", "GET", "/api/v1/manufacturing-costs")
