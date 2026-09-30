"""Read-only controls for the fixed fictional demo; no schema creation."""
from datetime import datetime
import sqlite3
from zoneinfo import ZoneInfo


def validate_demo(database: str, *, today: str | None = None) -> dict:
    today = today or datetime.now(ZoneInfo("Europe/Moscow")).date().isoformat()
    c = sqlite3.connect(f"file:{database}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    try:
        marker = dict(c.execute("SELECT key,value FROM app_meta WHERE key IN ('demo_learning_scenario_v1','demo_fixture_date')"))
        order = c.execute("SELECT id,status FROM procurement_orders WHERE source_key='demo-fifo-learning-procurement-v1'").fetchone()
        items = c.execute("SELECT posted_quantity FROM procurement_items WHERE order_id=?", (order['id'],)).fetchall() if order else []
        release = c.execute("SELECT total_cost_rub,packaging_cost_rub,status FROM manufacturing_releases WHERE source_key='demo-fifo-learning-release-v1|chrt:100001'").fetchone()
        movements = dict(c.execute("SELECT movement_type,status FROM inventory_movements WHERE source_task_key='demo-fifo-learning-dispatch-v1'"))
        events = c.execute("SELECT id,event_type,status,fifo_cost_rub,matched_sale_event_id FROM sales_fifo_events WHERE srid='DEMO-FIFO-LEARNING-001' ORDER BY event_date,event_type").fetchall()
        expense = c.execute("SELECT amount_rub FROM manual_expenses WHERE note LIKE 'DEMO-FIFO-CASE:%'").fetchall()
        cases = {
            'procurement': bool(order and order['status']=='Получено' and items and sum(i['posted_quantity'] for i in items)>=100),
            'production': bool(release and release['status']=='applied' and release['total_cost_rub']>0 and release['packaging_cost_rub']==120),
            'dispatch_receipt': movements.get('dispatch')=='closed' and movements.get('wb_receipt')=='applied',
            'sale_return_fifo': len(events)==2 and [e['event_type'] for e in events]==['sale','return'] and all(e['status']=='applied' and e['fifo_cost_rub']>0 for e in events) and events[1]['matched_sale_event_id']==events[0]['id'] and abs(events[1]['fifo_cost_rub']-events[0]['fifo_cost_rub'])<0.001,
            'manual_expense': len(expense)==1 and expense[0]['amount_rub']==120,
        }
        fresh = marker.get('demo_learning_scenario_v1')=='1' and marker.get('demo_fixture_date')==today
        return {'ok': fresh and all(cases.values()), 'fixture_date': marker.get('demo_fixture_date'), 'fresh': fresh, 'cases': cases}
    finally:
        c.close()
