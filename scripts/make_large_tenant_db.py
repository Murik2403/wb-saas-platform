"""Scale a seeded demo tenant database up to a large seller, for memory and speed measurements only.

The demo seed (13 products, 61 days) is copied and its rows multiplied: every copy gets its own products
(nm_id offset), order/sale ids and financial row ids, so the per-product metrics see `copies * 13` products and
`copies` times the rows. The result is SYNTHETIC. It is not a restored production backup and says so in `app_meta`.

    WB_SAAS_DATA_DIR=<seed dir> ... python scripts/make_large_tenant_db.py --src <seed>/wb_dashboard.sqlite3 --dst big.sqlite3 --copies 150
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path

NM_STEP = 1_000_000
RRD_STEP = 10_000_000

# table -> (columns that are shifted by copy index, id-text columns suffixed per copy)
TABLES = {
    "products_catalog": {"nm": ["nm_id"], "text": {"supplier_article": "-{k}"}},
    "costs": {"nm": ["nm_id"], "text": {"supplier_article": "-{k}"}},
    "stocks": {"nm": ["nm_id"], "text": {}},
    "orders": {"nm": ["nm_id"], "text": {"id": "-{k}", "supplier_article": "-{k}"}},
    "sales": {"nm": ["nm_id"], "text": {"id": "-{k}", "sale_id": "-{k}", "supplier_article": "-{k}"}},
    "ads_daily": {"nm": ["nm_id"], "shift": {"campaign_id": NM_STEP}, "text": {}},
    "financial_report": {"nm": ["nm_id"], "shift": {"rrd_id": RRD_STEP}, "text": {"supplier_article": "-{k}"}},
}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--src", required=True)
    parser.add_argument("--dst", required=True)
    parser.add_argument("--copies", type=int, required=True)
    args = parser.parse_args(argv)
    dst = Path(args.dst)
    shutil.copyfile(args.src, dst)
    conn = sqlite3.connect(dst)
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    for table, rule in TABLES.items():
        columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
        shifted = {**{column: NM_STEP for column in rule["nm"]}, **rule.get("shift", {})}
        conn.execute(f"CREATE TEMP TABLE {table}_base AS SELECT * FROM {table}")  # the originals, re-read for every copy
        names = ",".join(f'"{column}"' for column in columns)
        for k in range(1, args.copies):
            select = []
            for column in columns:
                if column in shifted:
                    select.append(f'"{column}" + {shifted[column] * k}')
                elif column in rule["text"]:
                    select.append(f"\"{column}\" || '{rule['text'][column].format(k=k)}'")
                else:
                    select.append(f'"{column}"')
            conn.execute(f"INSERT OR IGNORE INTO {table} ({names}) SELECT {','.join(select)} FROM {table}_base")
        conn.execute(f"DROP TABLE {table}_base")
    conn.commit()
    conn.execute("INSERT OR REPLACE INTO app_meta(key, value, updated_at) VALUES ('synthetic_scale_copies', ?, datetime('now'))", (str(args.copies),))
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    print(f"{dst}: {dst.stat().st_size / 1024 ** 2:.0f} MiB, copies={args.copies}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
