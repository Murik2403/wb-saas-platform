"""Typed central manufacturing consumption of managed, globally free ready FIFO."""
from decimal import Decimal
import math
from .incident_workflow import held_units

def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS warehouse_manufacturing_inputs(
      id INTEGER PRIMARY KEY,movement_id INTEGER NOT NULL,layer_id INTEGER NOT NULL,
      kind TEXT NOT NULL CHECK(kind IN ('consume','reverse')),units INTEGER NOT NULL CHECK(units>0),
      amount_rub REAL NOT NULL CHECK(amount_rub>=0),source_key TEXT NOT NULL,created_at TEXT NOT NULL,
      UNIQUE(movement_id,layer_id,kind))''')
    for operation in ('UPDATE','DELETE'):
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS warehouse_manufacturing_inputs_no_{operation.lower()} BEFORE {operation} ON warehouse_manufacturing_inputs BEGIN SELECT RAISE(ABORT,'central manufacturing lineage immutable'); END")
    conn.execute("CREATE TRIGGER IF NOT EXISTS warehouse_manufacturing_inputs_no_replace BEFORE INSERT ON warehouse_manufacturing_inputs WHEN EXISTS(SELECT 1 FROM warehouse_manufacturing_inputs WHERE id=NEW.id OR (movement_id=NEW.movement_id AND layer_id=NEW.layer_id AND kind=NEW.kind)) BEGIN SELECT RAISE(ABORT,'central manufacturing lineage immutable'); END")

def totals(conn,layer_id):
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='warehouse_manufacturing_inputs'").fetchone():return 0,0.0
    row=conn.execute("SELECT COALESCE(SUM(CASE kind WHEN 'consume' THEN units ELSE -units END),0) units,COALESCE(SUM(CASE kind WHEN 'consume' THEN amount_rub ELSE -amount_rub END),0) amount FROM warehouse_manufacturing_inputs WHERE layer_id=?",(layer_id,)).fetchone()
    return int(row['units']),float(row['amount'])

def consume(conn,movement_id,nm_id,chrt_id,quantity,document_date,source_key):
    from . import shared_warehouse as warehouse
    from .fifo_finished_goods import _update_finished_layer_status
    if not conn.in_transaction:raise warehouse.WarehouseError('Производственное списание требует общей транзакции')
    quantity=warehouse.integer(quantity);day=warehouse.day(document_date)
    movement=conn.execute("SELECT * FROM inventory_movements WHERE id=? AND movement_type='production_receipt' AND status='applied'",(movement_id,)).fetchone()
    if not movement or movement['source_task_key']!=source_key or str(movement['movement_date'])[:10]!=day:
        raise warehouse.WarehouseScopeError('Списание связано только с подтверждённым документом производства')
    if not warehouse.managed(conn,nm_id,chrt_id):raise warehouse.WarehouseScopeError('Это не общий SKU производства')
    layers=conn.execute("SELECT * FROM finished_goods_cost_layers WHERE nm_id=? AND chrt_id=? AND ready_units>0 AND status!='reversed' AND source_date IS NOT NULL AND source_date<=? AND source_type NOT IN ('synthetic_sale','return_unmatched','reconstruction_cost_pending') ORDER BY source_date,id LIMIT 1001",(nm_id,chrt_id,day)).fetchall()
    plan=[];remaining=quantity
    for layer in layers:
        if len(plan)>=1000:raise warehouse.WarehouseStockError('Слишком много производственных FIFO-партий')
        reserved=warehouse.reserved_units(conn,layer['id']);unreserved=layer['ready_units']-reserved
        take=min(remaining,max(0,unreserved-held_units(conn,layer['id'],'ready')))
        if not take:continue
        original=layer['original_amount_rub']
        if not math.isfinite(original) or original<0 or layer['original_units']<=0:raise warehouse.WarehouseStockError('Нет подтверждённой стоимости полуфабриката')
        claims=conn.execute("SELECT COALESCE(SUM(CASE WHEN status='allocated' THEN units ELSE 0 END),0) dispatched,COALESCE(SUM(cost_rub),0) amount FROM warehouse_reservation_layers WHERE layer_id=? AND status!='released'",(layer['id'],)).fetchone()
        consumed,consumed_amount=totals(conn,layer['id'])
        initial=layer['ready_units']+claims['dispatched']+consumed
        basis=conn.execute('SELECT * FROM warehouse_ready_basis WHERE layer_id=?',(layer['id'],)).fetchone()
        if initial>layer['original_units'] or (basis and basis['initial_ready_units']!=initial):raise warehouse.WarehouseConflict('Готовый запас изменён вне центрального учёта')
        basis_amount=basis['initial_ready_amount_rub'] if basis else warehouse.portion(original,initial,layer['original_units'])
        available_amount=warehouse.money(Decimal(str(basis_amount))-Decimal(str(claims['amount']))-Decimal(str(consumed_amount)))
        if available_amount<0:raise warehouse.WarehouseConflict('Стоимость распределений превышает исходную партию')
        amount=warehouse.portion(available_amount,take,unreserved)
        plan.append((layer,take,amount,initial,basis_amount));remaining-=take
        if not remaining:break
    if remaining:raise warehouse.WarehouseStockError('Недостаточно свободного общего полуфабриката; резерв и карантин сохраняются')
    allocations=[];total=0
    for layer,take,amount,initial,basis_amount in plan:
        if not conn.execute('SELECT 1 FROM warehouse_ready_basis WHERE layer_id=?',(layer['id'],)).fetchone():
            conn.execute('INSERT INTO warehouse_ready_basis VALUES(?,?,?,?)',(layer['id'],initial,basis_amount,warehouse.now()))
        conn.execute('UPDATE finished_goods_cost_layers SET ready_units=ready_units-?,updated_at=? WHERE id=?',(take,warehouse.now(),layer['id']))
        conn.execute("INSERT INTO finished_goods_fifo_allocations(movement_id,layer_id,units,amount_rub,from_location,to_location,status,created_at) VALUES(?,?,?,?,'ready','out','applied',?)",(movement_id,layer['id'],take,amount,warehouse.now()))
        conn.execute("INSERT INTO warehouse_manufacturing_inputs(movement_id,layer_id,kind,units,amount_rub,source_key,created_at) VALUES(?,?,'consume',?,?,?,?)",(movement_id,layer['id'],take,amount,source_key,warehouse.now()))
        _update_finished_layer_status(conn,layer['id'])
        allocations.append(dict(layer_id=layer['id'],units=take,amount_rub=amount));total+=amount
    return warehouse.money(total),allocations

def journal_reversal(conn,movement_id):
    """Append exact reversal after output dependency checks, before restoring FIFO."""
    from .shared_warehouse import WarehouseConflict,now
    rows=conn.execute("SELECT * FROM warehouse_manufacturing_inputs WHERE movement_id=? AND kind='consume'",(movement_id,)).fetchall()
    for row in rows:
        if conn.execute("SELECT 1 FROM warehouse_manufacturing_inputs WHERE movement_id=? AND layer_id=? AND kind='reverse'",(movement_id,row['layer_id'])).fetchone():raise WarehouseConflict('Производственное списание уже отменено')
        allocation=conn.execute("SELECT * FROM finished_goods_fifo_allocations WHERE movement_id=? AND layer_id=? AND status='applied' AND from_location='ready' AND to_location='out'",(movement_id,row['layer_id'])).fetchone()
        if not allocation or allocation['units']!=row['units'] or abs(allocation['amount_rub']-row['amount_rub'])>.001:raise WarehouseConflict('Производственная FIFO-история повреждена')
        conn.execute("INSERT INTO warehouse_manufacturing_inputs(movement_id,layer_id,kind,units,amount_rub,source_key,created_at) VALUES(?,?,'reverse',?,?,?,?)",(movement_id,row['layer_id'],row['units'],row['amount_rub'],row['source_key'],now()))
