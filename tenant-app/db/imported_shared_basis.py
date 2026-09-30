"""Immutable imported-local authority switch and its single central money resolver."""
from .finished_history_import import exists,cents

def ensure_schema(c):
 c.execute("""CREATE TABLE IF NOT EXISTS warehouse_imported_transfers(
   request_key TEXT PRIMARY KEY,nm_id INTEGER NOT NULL UNIQUE,identity TEXT NOT NULL,actor TEXT NOT NULL,
   reason TEXT NOT NULL,fingerprint TEXT NOT NULL,snapshot_json TEXT NOT NULL,created_at TEXT NOT NULL)""")
 c.execute("""CREATE TABLE IF NOT EXISTS warehouse_imported_authority(
   layer_id INTEGER PRIMARY KEY,nm_id INTEGER NOT NULL,request_key TEXT NOT NULL,
   ready_units INTEGER NOT NULL CHECK(ready_units>=0),ready_cents INTEGER NOT NULL CHECK(ready_cents>=0),
   source_ref TEXT NOT NULL,source_fingerprint TEXT NOT NULL,created_at TEXT NOT NULL)""")
 for table,key in [('warehouse_imported_transfers','request_key=NEW.request_key OR nm_id=NEW.nm_id'),('warehouse_imported_authority','layer_id=NEW.layer_id')]:
  for op in ('UPDATE','DELETE'):c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'imported authority immutable'); END")
  c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {key}) BEGIN SELECT RAISE(ABORT,'imported authority immutable'); END")

def transferred(c,layer_id):
 return exists(c,'warehouse_imported_authority') and bool(c.execute('SELECT 1 FROM warehouse_imported_authority WHERE layer_id=?',(layer_id,)).fetchone())

def sku_transferred(c,nm_id):
 return exists(c,'warehouse_imported_transfers') and bool(c.execute('SELECT 1 FROM warehouse_imported_transfers WHERE nm_id=?',(nm_id,)).fetchone())

def local_guard(c,layer_id):
 if transferred(c,layer_id):raise ValueError('Партия передана общему складу; используйте его подтверждённые движения')

def location_amount(c,layer_id,location,quantity):
 """Central ready includes held/reserved stock; ownership includes scoped holds."""
 if not transferred(c,layer_id):return None
 from .shared_warehouse import portion,WarehouseConflict
 layer=c.execute('SELECT * FROM finished_goods_cost_layers WHERE id=?',(layer_id,)).fetchone()
 basis=c.execute('SELECT * FROM warehouse_ready_basis WHERE layer_id=?',(layer_id,)).fetchone()
 if not basis:raise WarehouseConflict('Денежная основа перенесённой партии отсутствует')
 from .shared_manufacturing import totals
 consumed,amount=totals(c,layer_id)
 claims=c.execute("SELECT COALESCE(SUM(units),0),COALESCE(SUM(cost_rub),0) FROM warehouse_reservation_layers WHERE layer_id=? AND status='allocated'",(layer_id,)).fetchone()
 expected={'ready':basis['initial_ready_units']-claims[0]-consumed}
 monetary={'ready':cents(basis['initial_ready_amount_rub'])-cents(claims[1])-cents(amount)}
 for loc in ('inbound','wb'):
  owned=c.execute(f'SELECT COALESCE(SUM({loc}_units),0),COALESCE(SUM({loc}_cost_rub),0) FROM warehouse_ownership WHERE layer_id=?',(layer_id,)).fetchone()
  expected[loc]=owned[0];monetary[loc]=cents(owned[1])
 if any(layer[loc+'_units']!=expected[loc] or monetary[loc]<0 or expected[loc]<0 for loc in expected):raise WarehouseConflict('Перенесённая партия изменилась вне единого складского учёта')
 if quantity<0 or quantity>expected[location]:raise WarehouseConflict('Количество не соответствует подтверждённой денежной основе')
 return portion(monetary[location]/100,quantity,expected[location]) if quantity else 0.0
