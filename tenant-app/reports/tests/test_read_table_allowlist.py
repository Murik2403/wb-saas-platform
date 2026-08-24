"""Regression test: every table name reports/metrics.py passes to read_table()
must be present in db.core.read_table's own allowlist.

Caught live in production (2026-08-25): _wb_commission_rate() added
read_table("financial_report"), but tests only ever monkeypatch
metrics.read_table, so they never exercise the real allowlist in db/core.py --
the missing entry there raised ValueError("Недопустимая таблица") for every
report that included that metric, and no test caught it before deploy.
"""
import re

import reports.metrics as metrics
from db.core import read_table as real_read_table


def _table_names_used_by_metrics() -> set[str]:
    source = open(metrics.__file__, encoding="utf-8").read()
    return set(re.findall(r'read_table\("([a-z_]+)"\)', source))


def test_every_table_metrics_reads_is_in_the_real_allowlist():
    used = _table_names_used_by_metrics()
    assert used, "sanity check: metrics.py should reference at least one table via read_table()"
    for table_name in used:
        try:
            real_read_table(table_name)
        except ValueError as exc:
            assert False, f"read_table({table_name!r}) rejected by db.core's allowlist: {exc}"
        except Exception:
            # Any other error (e.g. missing DB file in this test environment) means
            # the name passed the allowlist check and failed later -- that's fine here,
            # we only care that it isn't rejected as an unknown table.
            pass
