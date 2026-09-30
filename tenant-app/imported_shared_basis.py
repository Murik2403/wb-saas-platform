"""Explicit primary-owner ready-only transfer; no physical or financial event."""
import json
from db.core import connect
from db import shared_warehouse as warehouse,finished_history_import as history,imported_shared_basis as authority
from db.incident_workflow import held_units
from db.wip import validate_catalog_identity
import shared_warehouse_setup as setup

def owner(c,account,role,read_only=False):
 if role!='owner' or not isinstance(account,str) or not account.isascii() or not account.isdigit() or str(int(account))!=account or not 0<int(account)<=2**63-1:
  raise warehouse.WarehouseScopeError('Переход подтверждает владелец из действующей сессии')
 if read_only:
  setup.actor(account,role)
  scope=warehouse.access(c,0,account,role,True)
  import config
  from db.shop_workspaces import account_identity
  token=config.get_token()
  if not token or account_identity(token)!=scope['identity']:raise warehouse.WarehouseScopeError('Подключение основного продавца изменилось')
  return scope
 return setup.access(c,0,account,role,True)

def initialize():
 setup.initialize()
 with connect() as c:authority.ensure_schema(c)

def _preview(c,nm_id,reason,account,role):
 scope=owner(c,account,role,True);nm_id=warehouse.integer(nm_id,maximum=2**53-1);reason=warehouse.text(reason,500)
 if len(reason)<3:raise warehouse.WarehouseConflict('Укажите основание переноса')
 if authority.sku_transferred(c,nm_id):raise warehouse.WarehouseConflict('Этот товар уже передан общему складу')
 if not history.exists(c,'migration_finished_lots'):raise warehouse.WarehouseConflict('Сначала перенесите готовые партии через CSV')
 lots=c.execute('SELECT * FROM migration_finished_lots WHERE nm_id=? ORDER BY source_id',(nm_id,)).fetchall()
 if not lots:raise warehouse.WarehouseConflict('Для этого товара нет импортированных готовых партий')
 if warehouse.managed(c,nm_id,0):raise warehouse.WarehouseConflict('Товар уже использует общий склад; повторная передача запрещена')
 card=c.execute('SELECT * FROM products_catalog WHERE nm_id=?',(nm_id,)).fetchone()
 if not card:raise warehouse.WarehouseConflict('Товар отсутствует в текущем каталоге')
 try:
  sizes=json.loads(card['raw_json']).get('sizes');ids=[r.get('chrtID',r.get('chrtId',r.get('chrt_id'))) for r in sizes] if sizes else [0]
  if not isinstance(sizes,list) or len(ids)>500 or len(set(ids))!=len(ids) or any(type(value) is not int or value<0 or value>2**53-1 for value in ids):raise ValueError()
  variants=[validate_catalog_identity(c,nm_id,value,True) for value in ids]
 except (ValueError,TypeError,AttributeError):raise warehouse.WarehouseConflict('Нужен полный однозначный каталог вариантов')
 layers=c.execute('SELECT * FROM finished_goods_cost_layers WHERE nm_id=? ORDER BY id',(nm_id,)).fetchall()
 if len(layers)>1000:raise warehouse.WarehouseConflict('Слишком много партий для одного перехода')
 plan=[]
 for layer in layers:
  if layer['source_type']!='migration_finished_opening' or layer['status']=='reversed':raise warehouse.WarehouseConflict('Для перехода допускаются только импортированные неотменённые партии')
  if layer['chrt_id'] not in ids:raise warehouse.WarehouseConflict('Вариант партии отсутствует в текущем каталоге')
  if layer['inbound_units'] or layer['wb_units']:raise warehouse.WarehouseConflict('Сначала разберите партии в пути и на WB; этот переход поддерживает только готовый остаток')
  if any(held_units(c,layer['id'],loc) for loc in ('ready','inbound','wb')):raise warehouse.WarehouseConflict('Сначала разберите карантин этой партии')
  if c.execute("SELECT 1 FROM finished_goods_fifo_allocations WHERE layer_id=? AND status='applied' AND reversed_at IS NULL",(layer['id'],)).fetchone():raise warehouse.WarehouseConflict('Есть действующие локальные движения; разберите зависимости и отмены до перехода')
  if c.execute('SELECT 1 FROM warehouse_ready_basis WHERE layer_id=?',(layer['id'],)).fetchone():raise warehouse.WarehouseConflict('Денежная основа уже используется распределителем')
  amount=history.physical_amount(c,layer['id'],'ready',layer['ready_units'])
  plan.append(dict(layer_id=layer['id'],chrt_id=layer['chrt_id'],ready_units=layer['ready_units'],ready_cents=history.cents(amount),source_ref=layer['source_ref'],source_fingerprint=warehouse.digest(dict(layer))))
 for lot in lots:
  if lot['original_units']!=lot['remaining_units'] and not c.execute('SELECT 1 FROM migration_historical_allocations WHERE lot_source_id=?',(lot['source_id'],)).fetchone():raise warehouse.WarehouseConflict('Сначала перенесите полную историю исходной партии: покрытие неизвестно')
 if sum(row['ready_units'] for row in plan)<=0:raise warehouse.WarehouseConflict('Нет готового остатка для передачи')
 pipeline=c.execute('SELECT * FROM product_pipeline WHERE nm_id=?',(nm_id,)).fetchone()
 if not pipeline or pipeline['ready_units']!=sum(row['ready_units'] for row in plan) or pipeline['inbound_units']:
  raise warehouse.WarehouseConflict('Локальный склад и импортированные партии не совпадают')
 result=dict(nm_id=nm_id,identity=scope['identity'],reason=reason,variants=variants,layers=plan,total_units=sum(r['ready_units'] for r in plan),total_cents=sum(r['ready_cents'] for r in plan),source_quality='manual_preliminary',old_authority='local_fifo',new_authority='shared_warehouse')
 state=dict(result=result,card=dict(card),lots=[dict(r) for r in lots],layers=[dict(r) for r in layers],pipeline=dict(pipeline))
 return dict(result,fingerprint=warehouse.digest(state))

def preview(nm_id,reason,account,role):
 with connect() as c:
  c.execute('BEGIN')
  return _preview(c,nm_id,reason,account,role)

def confirm(nm_id,reason,expected,account,role,request_key):
 initialize();body=dict(nm_id=nm_id,reason=reason,fingerprint=expected)
 with connect() as c:
  c.execute('BEGIN IMMEDIATE');scope=owner(c,account,role)
  old=setup._replay(c,request_key,0,scope['identity'],account,'imported_basis',body)
  if old is not None:return old
  result=_preview(c,nm_id,reason,account,role)
  if result['fingerprint']!=expected:raise warehouse.WarehouseConflict('Партии или каталог изменились; повторите проверку')
  timestamp=warehouse.now()
  c.execute('INSERT INTO warehouse_imported_transfers VALUES(?,?,?,?,?,?,?,?)',(request_key,nm_id,scope['identity'],account,reason,expected,warehouse.encoded(result),timestamp))
  for row in result['layers']:
   c.execute('INSERT INTO warehouse_imported_authority VALUES(?,?,?,?,?,?,?,?)',(row['layer_id'],nm_id,request_key,row['ready_units'],row['ready_cents'],row['source_ref'],row['source_fingerprint'],timestamp))
   c.execute('INSERT INTO warehouse_ready_basis VALUES(?,?,?,?)',(row['layer_id'],row['ready_units'],row['ready_cents']/100,timestamp))
  items=[warehouse.register_item(nm_id,v['chrt_id'],v['product_name'],_conn=c) for v in result['variants']]
  result={**result,'request_key':request_key,'items':items,'transferred_at':timestamp}
  owner(c,account,role,True)
  setup._receipt(c,request_key,0,scope['identity'],account,'imported_basis',body,result)
  return result
