import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tenant-app"))
sys.path.insert(0, str(ROOT / "tenant-app" / "pages"))

from production import build_wip_product_variant_options, filter_wip_summary_for_identity, wip_product_identity_label


def test_wip_selector_options_are_catalog_backed_variants():
    settings = pd.DataFrame([
        {"nm_id": 101, "enabled": 1, "supplier_article": "TEE", "material_name": "Cloth", "blank_type": "Body", "pack_size": 2},
    ])
    catalog = pd.DataFrame([
        {"nm_id": 101, "product_name": "Shirt", "raw_json": '{"sizes":[{"chrtID":501,"techSize":"S"},{"chrtID":502,"techSize":"M"}]}'},
    ])

    options = build_wip_product_variant_options(settings, catalog)

    assert [(item["nm_id"], item["chrt_id"], item["variant_label"]) for item in options] == [
        (101, 501, "S"), (101, 502, "M"),
    ]


def test_wip_packaging_available_units_never_combines_other_variants():
    summary = pd.DataFrame([
        {"nm_id": 101, "chrt_id": 501, "material_name": "Cloth", "blank_type": "Body", "remaining_units": 8},
        {"nm_id": 101, "chrt_id": 501, "material_name": "Other cloth", "blank_type": "Body", "remaining_units": 99},
        {"nm_id": 101, "chrt_id": 501, "material_name": "Cloth", "blank_type": "Sleeve", "remaining_units": 99},
        {"nm_id": 101, "chrt_id": 502, "material_name": "Cloth", "blank_type": "Body", "remaining_units": 4},
        {"nm_id": 0, "chrt_id": 0, "material_name": "Cloth", "blank_type": "Body", "remaining_units": 3},
    ])

    selected_variant = filter_wip_summary_for_identity(summary, 101, 501, "Cloth", "Body")
    base_product = filter_wip_summary_for_identity(summary, 101, 0, "Cloth", "Body")

    assert selected_variant["remaining_units"].sum() == 8
    assert base_product["remaining_units"].sum() == 3


def test_wip_selector_does_not_guess_identity_when_catalog_variant_data_is_invalid():
    settings = pd.DataFrame([
        {"nm_id": 101, "enabled": 1, "supplier_article": "TEE", "material_name": "Cloth", "blank_type": "Body", "pack_size": 2},
    ])
    catalog = pd.DataFrame([
        {"nm_id": 101, "product_name": "Shirt", "raw_json": '{"sizes":[{}]}'},
    ])

    assert build_wip_product_variant_options(settings, catalog) == []


def test_legacy_batches_are_labeled_unassigned():
    assert wip_product_identity_label(0, 0) == "\u0421\u0442\u0430\u0440\u044b\u0435 \u0434\u0430\u043d\u043d\u044b\u0435 \u0431\u0435\u0437 \u043f\u0440\u0438\u0432\u044f\u0437\u043a\u0438 \u043a \u0442\u043e\u0432\u0430\u0440\u0443 \u0438 \u0432\u0430\u0440\u0438\u0430\u043d\u0442\u0443"
