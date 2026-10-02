import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

import fbs_orders as fbs

NOW = datetime(2026, 9, 10, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    fbs.init(c)
    return c


def put(c, oid, created, sup, wb, upd=None, wh=1):
    c.execute("INSERT INTO fbs_order (order_id, nm_id, article, created_at, warehouse_id, supplier_status, wb_status, status_updated_at) "
              "VALUES (?,?,?,?,?,?,?,?)", (oid, 100, f"A{oid}", created, wh, sup, wb, upd))


def seed(c):
    put(c, 1, "2026-09-10T00:00:00Z", "new", "waiting")                                     # 12 h, not overdue
    put(c, 2, "2026-09-08T12:00:00Z", "confirm", "waiting", wh=2)                            # 48 h -> overdue
    put(c, 3, "2026-09-09T00:00:00Z", "complete", "sold", "2026-09-09T12:00:00Z")            # delivered after 12 h
    put(c, 4, "2026-09-09T00:00:00Z", "complete", "sold", "2026-09-10T00:00:00Z")            # delivered after 24 h
    put(c, 5, "2026-09-09T00:00:00Z", "complete", "canceled_by_client", "2026-09-10T00:00:00Z")
    put(c, 6, "2026-09-09T00:00:00Z", "cancel", "canceled", "2026-09-09T01:00:00Z")
    put(c, 7, "2026-09-09T00:00:00Z", "complete", "defect", "2026-09-09T05:00:00Z")
    put(c, 8, "2026-09-01T00:00:00Z", "new", "waiting")                                      # outside a 7-day window
    c.commit()


def test_state_of():
    assert fbs.state_of("complete", "sold") == "delivered"
    assert fbs.state_of("cancel", None) == "cancelled"
    assert fbs.state_of("complete", "declined_by_client") == "cancelled"
    assert fbs.state_of("complete", "ready_for_pickup") == "at_pickup"
    assert fbs.state_of("complete", "sorted") == "in_transit"
    assert fbs.state_of("confirm", "waiting") == "processing"
    assert fbs.state_of(None, None) == "unknown"


def test_analytics_hand_checked(conn):
    seed(conn)
    a = fbs.analytics(conn, days=7, now=NOW)
    counts = {s["key"]: s["count"] for s in a["by_status"]}
    assert a["total"] == 7 and counts["processing"] == 2 and counts["delivered"] == 2
    assert counts["cancelled"] == 2 and counts["defect"] == 1
    assert a["final_count"] == 5 and a["buyout_pct"] == 40.0            # 2 / (2 + 2 + 1)
    assert a["avg_delivery_hours"] == 18.0 and a["avg_delivery_sample"] == 2   # (12 + 24) / 2
    assert a["overdue_count"] == 1
    day9 = next(d for d in a["by_day"] if d["day"] == "2026-09-09")
    assert day9 == {"day": "2026-09-09", "total": 5, "delivered": 2, "cancelled": 3}


def test_analytics_empty_is_null(conn):
    a = fbs.analytics(conn, days=7, now=NOW)
    assert a["total"] == 0 and a["buyout_pct"] is None and a["avg_delivery_hours"] is None
    put(conn, 1, "2026-09-10T00:00:00Z", "new", "waiting")
    a = fbs.analytics(conn, days=7, now=NOW)
    assert a["buyout_pct"] is None and a["avg_delivery_hours"] is None  # no final orders -> not computable


def test_list_filters_and_paging(conn):
    seed(conn)
    r = fbs.list_orders(conn, days=7, now=NOW)
    assert r["total"] == 7 and r["items"][0]["order_id"] == 1  # newest first
    over = fbs.list_orders(conn, overdue=True, days=7, now=NOW)
    assert [i["order_id"] for i in over["items"]] == [2] and over["items"][0]["age_hours"] == 48.0
    assert fbs.list_orders(conn, status="delivered", days=7, now=NOW)["total"] == 2
    assert fbs.list_orders(conn, warehouse=2, days=7, now=NOW)["total"] == 1
    assert [w["id"] for w in r["warehouses"]] == [1, 2]
    assert len(fbs.list_orders(conn, days=7, limit=3, offset=5, now=NOW)["items"]) == 2
    assert "_start" not in r["items"][0]


class FakeApi:
    def __init__(self):
        self.status_calls, self.order_calls = [], []

    def get_fbs_orders(self, date_from, date_to, limit, cursor):
        self.order_calls.append(cursor)
        if cursor == 0:
            return {"orders": [{"id": 1, "nmId": 5, "article": "X", "createdAt": "2026-09-10T08:00:00Z", "price": 150050, "warehouseId": 9},
                               {"id": 2, "createdAt": "2026-09-10T09:00:00Z"}, {"nmId": 1}], "next": 2}
        return {"orders": [{"id": 3, "createdAt": "2026-09-10T10:00:00Z"}], "next": 0}

    def get_fbs_order_statuses(self, ids):
        self.status_calls.append(list(ids))
        return [{"id": i, "supplierStatus": "new", "wbStatus": "waiting"} for i in ids]


def test_refresh_flow_and_min_interval(conn):
    api = FakeApi()
    st = fbs.refresh(conn, api, now=NOW, pause=0, has_scope=True)
    assert st["last_error"] is None and st["orders_total"] == 3 and st["last_result"]["pages"] == 2
    row = conn.execute("SELECT price, warehouse_id, supplier_status, status_updated_at FROM fbs_order WHERE order_id = 1").fetchone()
    assert row == (1500.5, 9, "new", "2026-09-10T12:00:00Z")
    assert sorted(api.status_calls[0]) == [1, 2, 3]
    with pytest.raises(fbs.TooSoon) as e:
        fbs.refresh(conn, api, now=NOW + timedelta(seconds=100), pause=0)
    assert e.value.retry_after == 201
    # after the interval: finished order is no longer re-queried, changed status moves the timestamp
    conn.execute("UPDATE fbs_order SET supplier_status='complete', wb_status='sold' WHERE order_id = 2")
    conn.commit()
    api.status_calls.clear()
    fbs.refresh(conn, api, now=NOW + timedelta(seconds=301), pause=0)
    assert sorted(api.status_calls[0]) == [1, 3]


def test_status_change_updates_timestamp_only_on_change(conn):
    put(conn, 1, "2026-09-10T00:00:00Z", "new", "waiting", "2026-09-10T01:00:00Z")
    fbs._apply_statuses(conn, [{"id": 1, "supplierStatus": "new", "wbStatus": "waiting"}], NOW)
    assert conn.execute("SELECT status_updated_at FROM fbs_order").fetchone()[0] == "2026-09-10T01:00:00Z"
    fbs._apply_statuses(conn, [{"id": 1, "supplierStatus": "complete", "wbStatus": "sorted"}], NOW)
    assert conn.execute("SELECT status_updated_at FROM fbs_order").fetchone()[0] == "2026-09-10T12:00:00Z"


def test_missing_scope_makes_no_wb_call(conn):
    api = FakeApi()
    st = fbs.refresh(conn, api, now=NOW, pause=0, has_scope=False)
    assert st["last_error"] == fbs.NO_SCOPE_MESSAGE and not api.order_calls


def test_wb_failure_is_stored_not_raised(conn):
    class Boom(FakeApi):
        def get_fbs_orders(self, *a):
            raise RuntimeError("WB API 403: forbidden secret-detail")
    st = fbs.refresh(conn, Boom(), now=NOW, pause=0)
    assert st["last_error"] == fbs.NO_SCOPE_MESSAGE and "secret" not in st["last_error"] and st["last_success_at"] is None
    st = fbs.refresh(conn, FakeApi(), now=NOW + timedelta(seconds=400), pause=0)
    assert st["last_error"] is None and st["last_success_at"]


def test_tolerant_parsing():
    assert fbs.map_order({}) is None
    m = fbs.map_order({"id": "7", "createdAt": "garbage", "price": "x"})
    assert m["order_id"] == 7 and m["created_at"] is None and m["price"] is None
