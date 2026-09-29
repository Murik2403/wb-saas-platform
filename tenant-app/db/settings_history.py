"""Append-only, non-secret snapshots of manual product settings."""
from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Mapping
from typing import Any
from datetime import datetime, timezone

FIELDS = {
    "cost": {"supplier_article": str, "cost_per_wb_unit": float, "auto_material_cost": bool, "material_cost_rub": float, "packaging_cost_rub": float, "packaging_cost_status": str, "packaging_cost_source": str, "labor_cost_rub": float, "other_cost_rub": float},
    "production_norm": {"supplier_article": str, "enabled": bool, "material_per_unit": float, "target_days": int, "min_batch": int, "blank_type": str, "pack_size": int, "auto_rules": bool, "material_name": str},
}


def normalized_snapshot(kind: str, value: Mapping[str, Any] | sqlite3.Row | None) -> dict[str, Any] | None:
    if kind not in FIELDS:
        raise ValueError("Unknown settings history kind")
    if value is None:
        return None
    value = dict(value)
    result = {}
    for name, convert in FIELDS[kind].items():
        if name in value:
            raw = value[name]
            result[name] = convert(raw if raw is not None else ("" if convert is str else 0))
            if convert is float and not math.isfinite(result[name]):
                raise ValueError("Settings history requires finite values")
    return result


def append_settings_change(
    conn: sqlite3.Connection, *, kind: str, nm_id: int,
    before: Mapping[str, Any] | sqlite3.Row | None,
    after: Mapping[str, Any] | sqlite3.Row | None,
    source: str, actor: str | None = None,
) -> bool:
    before = normalized_snapshot(kind, before)
    after = normalized_snapshot(kind, after)
    if before == after:
        return False
    if after is None or int(nm_id) <= 0:
        raise ValueError("Invalid settings change")
    conn.execute(
        "INSERT INTO settings_change_history(kind,nm_id,before_json,after_json,changed_at,source,actor) VALUES(?,?,?,?,?,?,?)",
        (kind, int(nm_id), json.dumps(before, ensure_ascii=False, sort_keys=True) if before is not None else None,
         json.dumps(after, ensure_ascii=False, sort_keys=True), datetime.now(timezone.utc).isoformat(timespec="microseconds"), source, actor or None),
    )
    return True
