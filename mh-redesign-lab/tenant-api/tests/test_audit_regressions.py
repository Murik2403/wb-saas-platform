import pandas as pd
import pytest
import asyncio
import sqlite3
from types import SimpleNamespace
from pydantic import ValidationError
from test_display_contract import api
from ads_service import compute_ads


def test_costs_survive_missing_catalogue(monkeypatch):
    costs = pd.DataFrame([dict(nm_id=123, supplier_article="sku", cost_per_wb_unit=45.5, note="saved")])
    monkeypatch.setattr(api, "read_table", lambda name: costs if name == "costs" else pd.DataFrame())
    assert api.get_product_costs()["items"][0]["cost_per_wb_unit"] == 45.5


def test_production_norms_are_exposed_with_catalogue_names(monkeypatch):
    settings = pd.DataFrame([dict(
        nm_id=123, supplier_article="placemat-4", enabled=1,
        material_per_unit=0.447, min_batch=10, pack_size=4,
        blank_type="Старые болванки",
    )])
    catalog = pd.DataFrame([dict(nm_id=123, product_name="Плейсматы 4 шт")])
    monkeypatch.setattr(api, "read_table", lambda name: {"production_settings": settings, "products_catalog": catalog}.get(name, pd.DataFrame()))
    item = api.get_production_norms()["items"][0]
    assert item == {
        "nm_id": 123, "supplier_article": "placemat-4", "product_name": "Плейсматы 4 шт",
        "enabled": 1, "material_per_unit": 0.447, "min_batch": 10,
        "pack_size": 4, "blank_type": "Старые болванки",
        "material_name": "", "material_unit": "м",
    }


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_cost_rejects_non_finite_input(value):
    with pytest.raises(ValidationError):
        api.ProductCostInput(nm_id=123, cost_per_wb_unit=value)


def test_successful_write_invalidates_dashboard(monkeypatch):
    monkeypatch.setattr(api, "save_costs", lambda frame, **kwargs: None)
    api.dashboard_response_cache.get_or_build("audit", lambda: "old")
    async def call_next(request):
        api.update_product_costs(api.ProductCostsUpdateInput(items=[api.ProductCostInput(nm_id=123, cost_per_wb_unit=45)]), SimpleNamespace(headers={}))
        return SimpleNamespace(status_code=200)
    response = asyncio.run(api.invalidate_after_write(SimpleNamespace(method="PATCH", url=SimpleNamespace(path="/api/v1/settings/product-costs", scheme="http")), call_next))
    assert response.status_code == 200
    assert api.dashboard_response_cache.get_or_build("audit", lambda: "new") == "new"


def test_demo_rejects_write_before_handler(monkeypatch):
    monkeypatch.setattr(api, "IS_DEMO", True, raising=False)
    async def call_next(request):
        pytest.fail("Demo must not reach a write handler")
    response = asyncio.run(api.invalidate_after_write(SimpleNamespace(method="POST", url=SimpleNamespace(path="/api/v1/settings/product-costs", scheme="http")), call_next))
    assert response.status_code == 403


def test_partial_cost_coverage_is_reported(monkeypatch, tmp_path):
    import sqlite3
    from test_display_contract import setup_sync
    setup_sync(monkeypatch, tmp_path, [], True)
    with sqlite3.connect(api.DB_PATH) as conn:
        conn.execute("CREATE TABLE costs(nm_id INTEGER, cost_per_wb_unit REAL)")
        conn.execute("CREATE TABLE products_catalog(nm_id INTEGER)")
        conn.executemany("INSERT INTO products_catalog VALUES(?)", [(1,), (2,)])
        conn.execute("INSERT INTO costs VALUES(1, 25)")
    assert api.get_data_status()["missing_costs"] is True


def test_data_status_reports_missing_material_inventory(monkeypatch, tmp_path):
    from test_display_contract import setup_sync
    setup_sync(monkeypatch, tmp_path, [(1, "success", "2026-09-09T20:00:00")], True)
    with sqlite3.connect(api.DB_PATH) as conn:
        conn.execute("CREATE TABLE production_settings(nm_id INTEGER, enabled INTEGER)")
        conn.execute("CREATE TABLE material_inventory_color(material_key TEXT, balance_known INTEGER)")
        conn.execute("INSERT INTO production_settings VALUES(1, 1)")
    assert api.get_data_status()["missing_material_inventory"] is True
    with sqlite3.connect(api.DB_PATH) as conn:
        conn.execute("INSERT INTO material_inventory_color VALUES('beige', 1)")
    assert api.get_data_status()["missing_material_inventory"] is False


def test_production_dashboard_shows_recommendations_when_shift_is_empty(monkeypatch):
    api.dashboard_response_cache.clear()
    monkeypatch.setattr(api, "_today_dashboard", lambda: ({"tabs": {"shift": {
        "planned": 0, "material_covered": 0, "tasks": [],
        "recommendations": [{"article": "grei182258", "product": "Плейсматы", "planned": 40, "status": "Рекомендовано"}],
    }, "material": {"rows": []}}}, object()))
    monkeypatch.setattr(api, "read_table", lambda _: pd.DataFrame())
    result = api.get_dashboard_production()
    assert result["tasks"] == [{"article": "grei182258", "product": "Плейсматы", "planned": 40, "status": "Рекомендовано"}]
    assert result["metrics"][0]["value"] == 40
    assert result["metrics"][1]["value"] == 40


def test_created_production_shift_is_resolved_and_visible(monkeypatch, tmp_path):
    database = tmp_path / "tenant.sqlite3"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE production_settings(nm_id INTEGER, supplier_article TEXT)")
        conn.execute("CREATE TABLE products_catalog(nm_id INTEGER, product_name TEXT)")
        conn.execute("INSERT INTO production_settings VALUES(123, 'placemat-4')")
        conn.execute("INSERT INTO products_catalog VALUES(123, 'Плейсматы 4 шт')")
    saved = []
    monkeypatch.setattr(api, "DB_PATH", database)
    monkeypatch.setattr(api, "save_execution_tasks", lambda frame: saved.extend(frame.to_dict("records")))
    payload = api.ProductionShiftInput(tasks=[api.ProductionTaskInput(
        task_key="shift:1", supplier_article="placemat-4", product_name="",
        planned_units=20, task_date="2026-09-10",
    )])

    assert api.create_production_shift(payload) == {"saved": 1, "status": "Не начато"}
    assert saved[0]["task_type"] == "production"
    assert saved[0]["nm_id"] == 123
    assert saved[0]["product_name"] == "Плейсматы 4 шт"


def test_production_execution_hides_closed_and_keeps_legacy_type(monkeypatch):
    rows = pd.DataFrame([
        {"task_key": "open", "task_type": "production", "status": "Не начато"},
        {"task_key": "legacy", "task_type": "Производство", "status": "В работе"},
        {"task_key": "closed", "task_type": "production", "status": "Закрыто"},
        {"task_key": "supply", "task_type": "procurement", "status": "В работе"},
    ])
    monkeypatch.setattr(api, "read_table", lambda _: rows)

    assert [row["task_key"] for row in api.get_production_execution()["tasks"]] == ["open", "legacy"]


def test_revenue_trend_uses_daily_revenue_from_dashboard(monkeypatch):
    daily = pd.DataFrame([
        {"day": "2026-09-08", "revenue": 1200.0},
        {"day": "2026-09-09", "revenue": 2450.5},
    ])
    monkeypatch.setattr(api, "build_dashboard", lambda start, end: SimpleNamespace(daily=daily))
    assert api._revenue_trend_days(2) == [
        {"date": "2026-09-08", "revenue_rub": 1200.0},
        {"date": "2026-09-09", "revenue_rub": 2450.5},
    ]


def test_ads_payload_exposes_funnel_and_product_signals():
    data = SimpleNamespace(
        kpi={"ad_revenue": 1_200.0, "ad_spend": 120.0, "ad_drr": 10.0, "ad_ctr": 5.0, "ad_clicks": 50},
        ads=pd.DataFrame([{
            "Кампания": 42, "Расход": 120.0, "Показы": 1_000, "Клики": 50,
            "Заказы": 8, "Рекламная выручка": 1_200.0, "CTR, %": 5.0,
            "CPC": 2.4, "ДРР, %": 10.0,
        }]),
        products=pd.DataFrame([{
            "Артикул продавца": "placemat-4", "Товар": "Плейсматы", "Реклама": 120.0,
            "Выручка": 1_200.0, "ДРР, %": 10.0,
        }]),
    )

    payload = compute_ads({"data": data, "period": "30d", "start": "2026-08-11", "end": "2026-09-09"})

    assert payload["funnel"] == [
        {"label": "Показы", "value": 1000, "value_text": "1 000", "width_pct": 100},
        {"label": "Клики", "value": 50, "value_text": "50", "width_pct": 5.0},
        {"label": "Заказы", "value": 8, "value_text": "8", "width_pct": 0.8},
    ]
    assert payload["creative_tests"] == [{
        "label": "placemat-4", "value": "ДРР 10.0% · 120 ₽", "winner": True,
    }]


def test_operational_panels_use_stock_and_material_inventory(monkeypatch):
    data = SimpleNamespace(products=pd.DataFrame([
        {"Артикул продавца": "critical-sku", "Товар": "Товар", "Остаток": 3, "Запас, дней": 1.5},
        {"Артикул продавца": "safe-sku", "Товар": "Товар", "Остаток": 80, "Запас, дней": 22},
    ]))
    assert api._stock_coverage(data)[0] == {
        "label": "critical-sku", "detail": "Товар", "value": "3 шт. · 1.5 дн.", "width": 7, "tone": "danger",
    }

    settings = pd.DataFrame([{"enabled": 1}, {"enabled": 1}])
    materials = pd.DataFrame([{
        "material_key": "beige", "material_name": "Бежевый", "balance_known": 1,
        "full_rolls": 2, "roll_length": 25.5, "partial_meters": 1.0,
    }])
    monkeypatch.setattr(api, "read_table", lambda name: settings if name == "production_settings" else materials)
    stages, rows = api._production_panels()
    assert stages[0] == {"label": "Нормы", "value": "2 SKU настроено", "status": "done"}
    assert rows == [{"name": "Бежевый", "need": "2 упак. по 25.5 м", "stock": "52.000 м", "status": "остаток подтверждён", "tone": "good"}]
    materials.loc[0, "unit"] = "кг"
    materials.loc[0, "tracking_mode"] = "quantity"
    materials.loc[0, "full_rolls"] = 0
    materials.loc[0, "roll_length"] = 1_000_000_000
    _, rows = api._production_panels()
    assert rows[0]["stock"] == "1.000 кг"
    assert rows[0]["need"] == "Учёт по количеству"


def test_shared_context_is_copied_between_screens(monkeypatch):
    api.context_response_cache.clear()
    calls = []
    def build():
        calls.append(1)
        return {"items": [1]}
    monkeypatch.setattr(api, "_build_ctx_uncached", build)
    first = api._build_ctx()
    first["items"].append(2)
    assert api._build_ctx()["items"] == [1]
    assert len(calls) == 1
    api.context_response_cache.clear()


def test_ads_retry_resolves_only_advertising_failure(monkeypatch, tmp_path):
    import sqlite3
    from test_display_contract import setup_sync
    setup_sync(monkeypatch, tmp_path, [(1, "partial", "2026-09-09T20:00:00"), (2, "success", "2026-09-09T21:00:00")], True)
    with sqlite3.connect(api.DB_PATH) as conn:
        conn.execute("UPDATE sync_log SET details='ads_error: WB API 500' WHERE id=1")
        conn.execute("UPDATE sync_log SET details='scope: ads, ads: 999, campaigns: 175' WHERE id=2")
    assert api.get_data_status()["partial"] is False
    with sqlite3.connect(api.DB_PATH) as conn:
        conn.execute("UPDATE sync_log SET details='financial_error: failure, ads_error: WB API 500' WHERE id=1")
    assert api.get_data_status()["partial"] is True


@pytest.mark.parametrize(
    ("handler", "expected_key"),
    [
        (api.get_overview, "kpis"),
        (api.get_products, "products"),
        (api.get_ads, "campaigns"),
        (api.get_dashboard_ads, "metrics"),
    ],
)
def test_empty_tenant_pages_return_usable_payload(monkeypatch, handler, expected_key):
    monkeypatch.setattr(api, "table_count", lambda _name: 0)
    payload = handler(period="7d")
    assert payload[expected_key] == []
    assert payload["empty_reason"] == "no_data_yet"


def test_legacy_health_endpoints_return_ok():
    assert api.legacy_health() == {"status": "ok"}
