"""Explicit manual opening snapshot + complete archived history; no historical physical replay."""
import csv,io,json,hashlib
from collections import defaultdict
from datetime import date,datetime,timezone
from decimal import Decimal,ROUND_HALF_UP
from .wip import validate_catalog_identity

FIELDS={
 'finished_lots':['source_id','nm_id','chrt_id','source_date','cutover_date','location','original_units','remaining_units','unit_cost_rub','remaining_cost_rub','document_proof'],
 'historical_documents':['source_id','document_id','rrd_id','nm_id','chrt_id','operation_date','sequence','event_type','quantity','lot_source_id','allocation_units','original_sale_document','retail_amount','ppvz_for_pay','commission','delivery_rub','storage_fee','deduction','penalty','acceptance','document_proof']}
MONEY=['retail_amount','ppvz_for_pay','commission','delivery_rub','storage_fee','deduction','penalty','acceptance']

def dump(v):return json.dumps(v,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
def digest(v):return hashlib.sha256(dump(v).encode()).hexdigest()
def exists(c,t):return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(t,)).fetchone())
def cents(v):return int((Decimal(str(v))*100).quantize(Decimal('1'),rounding=ROUND_HALF_UP))
def part(amount,take,units):return int((Decimal(amount)*take/units).quantize(Decimal('1'),rounding=ROUND_HALF_UP)) if take<units else amount

def schema(c):
 c.execute("""CREATE TABLE IF NOT EXISTS migration_finished_lots(source_id TEXT PRIMARY KEY,shop_id INTEGER NOT NULL CHECK(shop_id=0),nm_id INTEGER NOT NULL,chrt_id INTEGER NOT NULL,cutover_date TEXT NOT NULL,original_units INTEGER NOT NULL,remaining_units INTEGER NOT NULL,original_cents INTEGER NOT NULL,remaining_cents INTEGER NOT NULL,layer_id INTEGER,snapshot_json TEXT NOT NULL)""")
 c.execute("""CREATE TABLE IF NOT EXISTS migration_historical_documents(document_id TEXT PRIMARY KEY,rrd_id INTEGER NOT NULL UNIQUE,shop_id INTEGER NOT NULL CHECK(shop_id=0),nm_id INTEGER NOT NULL,chrt_id INTEGER NOT NULL,operation_date TEXT NOT NULL,event_type TEXT NOT NULL,quantity INTEGER NOT NULL,cogs_cents INTEGER NOT NULL,financial_fingerprint TEXT NOT NULL,snapshot_json TEXT NOT NULL)""")
 c.execute("""CREATE TABLE IF NOT EXISTS migration_historical_allocations(source_id TEXT PRIMARY KEY,document_id TEXT NOT NULL,lot_source_id TEXT NOT NULL,units INTEGER NOT NULL,cost_cents INTEGER NOT NULL,original_sale_document TEXT,snapshot_json TEXT NOT NULL)""")
 for table,key in [('migration_finished_lots','source_id'),('migration_historical_documents','document_id'),('migration_historical_allocations','source_id')]:
  for op in ('UPDATE','DELETE'):
   c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'CSV provenance immutable'); END")
  extra=" OR rrd_id=NEW.rrd_id" if table=="migration_historical_documents" else ""
  c.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE {key}=NEW.{key}{extra}) BEGIN SELECT RAISE(ABORT,'CSV provenance immutable'); END")
 c.execute("CREATE TRIGGER IF NOT EXISTS migration_layer_no_delete BEFORE DELETE ON finished_goods_cost_layers WHEN OLD.source_type='migration_finished_opening' BEGIN SELECT RAISE(ABORT,'CSV layer provenance immutable'); END")
 c.execute("CREATE TRIGGER IF NOT EXISTS migration_layer_no_replace BEFORE INSERT ON finished_goods_cost_layers WHEN EXISTS(SELECT 1 FROM finished_goods_cost_layers WHERE source_type='migration_finished_opening' AND (id=NEW.id OR (source_type=NEW.source_type AND source_ref=NEW.source_ref))) BEGIN SELECT RAISE(ABORT,'CSV layer provenance immutable'); END")
 c.execute("CREATE TRIGGER IF NOT EXISTS migration_layer_header BEFORE UPDATE ON finished_goods_cost_layers WHEN OLD.source_type='migration_finished_opening' AND (NEW.nm_id!=OLD.nm_id OR NEW.chrt_id!=OLD.chrt_id OR NEW.source_type!=OLD.source_type OR NEW.source_ref!=OLD.source_ref OR NEW.source_date!=OLD.source_date OR NEW.original_units!=OLD.original_units OR NEW.unit_cost_rub!=OLD.unit_cost_rub OR NEW.original_amount_rub!=(SELECT remaining_cents/100.0 FROM migration_finished_lots WHERE source_id=substr(OLD.source_ref,7))) BEGIN SELECT RAISE(ABORT,'CSV layer provenance immutable'); END")
 for table in ('inventory_movements','finished_goods_cost_layers'):
  field='movement_date' if table=='inventory_movements' else 'source_date'
  for op in ('INSERT','UPDATE'):
   c.execute(f"""CREATE TRIGGER IF NOT EXISTS migration_cutover_{table}_{op.lower()} BEFORE {op} ON {table} WHEN EXISTS(SELECT 1 FROM migration_finished_lots WHERE nm_id=NEW.nm_id AND (NEW.{field} IS NULL OR substr(NEW.{field},1,10)<cutover_date)) BEGIN SELECT RAISE(ABORT,'CSV cutover: backdated physical operation forbidden'); END""")

def cutoff(c,nm_id):
 if not exists(c,'migration_finished_lots'):return None
 row=c.execute('SELECT MAX(cutover_date) day FROM migration_finished_lots WHERE nm_id=?',(nm_id,)).fetchone()
 return row['day'] if row else None

def parse(kind,text,mapping):
 if len(text.encode('utf-8'))>1000000 or '\x00' in text:raise ValueError('CSV больше 1 МБ или содержит недопустимый символ')
 if set(mapping)!=set(FIELDS[kind]) or len(set(mapping.values()))!=len(mapping):raise ValueError('Сопоставьте все обязательные поля отдельно')
 try:delimiter=csv.Sniffer().sniff(text[:4096],delimiters=',;\t').delimiter
 except csv.Error:delimiter=';'
 reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')),delimiter=delimiter)
 if not reader.fieldnames or len(set(reader.fieldnames))!=len(reader.fieldnames) or any(x not in reader.fieldnames for x in mapping.values()):raise ValueError('Неверные или повторные заголовки CSV')
 rows=[];errors=[];seen=set()
 for line,raw in enumerate(reader,2):
  if line>1001:raise ValueError('Не более 1000 строк')
  try:
   if None in raw or any(v is None for v in raw.values()):raise ValueError('Количество ячеек не совпадает')
   row={f:raw[mapping[f]].strip() for f in FIELDS[kind]}
   if any(not v or len(v)>300 for v in row.values()):raise ValueError('Все поля обязательны, до 300 символов')
   if row['source_id'] in seen:raise ValueError('Повтор source_id')
   seen.add(row['source_id'])
   numbers=['nm_id','chrt_id','original_units','remaining_units'] if kind=='finished_lots' else ['nm_id','chrt_id','rrd_id','sequence','quantity','allocation_units']
   for f in numbers:
    v=row[f]
    if not v.isascii() or not v.isdigit() or len(v)>16:raise ValueError('Идентификатор/количество должно быть целым')
    row[f]=int(v);limit=2**53-1 if f in ('nm_id','chrt_id','rrd_id') else 1000000000
    if row[f]>limit or (row[f]==0 and f not in ('chrt_id','remaining_units')):raise ValueError('Идентификатор/количество вне диапазона')
   for f in (['unit_cost_rub','remaining_cost_rub'] if kind=='finished_lots' else MONEY):
    val=Decimal(row[f].replace(',','.'));precision=6 if f=='unit_cost_rub' else 2
    if not val.is_finite() or val<(0 if kind=='finished_lots' else -1000000000) or val>1000000000 or val.as_tuple().exponent < -precision:raise ValueError('Стоимость: конечное число до 1 млрд, рубли до2знаков/ставка до6')
    row[f]=str(val)
   days=['source_date','cutover_date'] if kind=='finished_lots' else ['operation_date']
   for f in days:
    row[f]=date.fromisoformat(row[f]).isoformat()
    if date.fromisoformat(row[f])>date.today():raise ValueError('Дата не может быть из будущего')
   if len(row['document_proof'])<3:raise ValueError('Укажите документ и основание ручного переноса')
   if kind=='finished_lots':
    if row['location'] not in ('ready','inbound','wb') or row['source_date']>row['cutover_date']:raise ValueError('Неверное место/дата партии')
    if row['remaining_units']>row['original_units']:raise ValueError('Остаток больше исходной партии')
    orig=cents(Decimal(row['unit_cost_rub'])*row['original_units']);remain=cents(row['remaining_cost_rub'])
    if orig>100000000000 or remain>orig or (row['remaining_units']==0 and remain!=0) or (row['remaining_units']==row['original_units'] and remain!=orig):raise ValueError('Денежный остаток не совпадает с количеством/исходной стоимостью')
   elif row['event_type'] not in ('sale','return') or (row['event_type']=='sale' and row['original_sale_document']!='-') or (row['event_type']=='return' and row['original_sale_document']=='-'):raise ValueError('Возврат требует явного исходного документа продажи')
   if kind=='historical_documents' and ((row['event_type']=='sale' and (Decimal(row['retail_amount'])<0 or Decimal(row['ppvz_for_pay'])<0)) or (row['event_type']=='return' and (Decimal(row['retail_amount'])>0 or Decimal(row['ppvz_for_pay'])>0))):raise ValueError('Продажа: выручка/выплата >=0; возврат: <=0. Удержания имеют собственный знак источника')
   rows.append({'line':line,'entity_key':row['source_id'],'data':row})
  except (ValueError,ArithmeticError) as e:errors.append({'line':line,'message':str(e)})
 if not rows and not errors:raise ValueError('В CSV нет строк')
 return rows,errors

def snapshot(c):
 names=['migration_finished_lots','migration_historical_documents','migration_historical_allocations']
 return {t:[dict(r) for r in c.execute('SELECT * FROM '+t)] if exists(c,t) else [] for t in names}

def preview(c,kind,text,mapping):
 rows,errors=parse(kind,text,mapping);state=snapshot(c);cards={};plans=[]
 def error(item,msg):errors.append({'line':item['line'],'message':str(msg)})
 for item in rows:
  r=item['data'];nm=r['nm_id']
  try:
   validate_catalog_identity(c,nm,r['chrt_id'],True)
   cards[str(nm)]=dict(c.execute('SELECT * FROM products_catalog WHERE nm_id=?',(nm,)).fetchone())
   from .shared_warehouse import managed
   if managed(c,nm,r['chrt_id']):raise ValueError('Общий SKU переносится отдельным центральным документом; этот мастер не меняет общий склад')
  except ValueError as e:error(item,e)
 if kind=='finished_lots':
  bynm=defaultdict(list)
  for item in rows:bynm[item['data']['nm_id']].append(item)
  state['physical']={}
  for nm,items in bynm.items():
   layers=[dict(r) for r in c.execute('SELECT * FROM finished_goods_cost_layers WHERE nm_id=?',(nm,))]
   movements=[dict(r) for r in c.execute('SELECT * FROM inventory_movements WHERE nm_id=?',(nm,))]
   pipeline=c.execute('SELECT * FROM product_pipeline WHERE nm_id=?',(nm,)).fetchone()
   latest=c.execute('SELECT MAX(snapshot_at) FROM stocks WHERE nm_id=?',(nm,)).fetchone()[0]
   stocks=[dict(r) for r in c.execute('SELECT * FROM stocks WHERE nm_id=? AND snapshot_at=?',(nm,latest))] if latest else []
   state['physical'][str(nm)]=[layers,movements,dict(pipeline) if pipeline else None,stocks]
   if layers or movements or any(l['nm_id']==nm for l in state['migration_finished_lots']):error(items[0],'У артикула уже есть FIFO/движения/перенос: замена запрещена')
   if pipeline and (pipeline['ready_units'] or pipeline['inbound_units']):error(items[0],'Локальный готовый остаток/товар в пути уже существует')
   if sum(i['data']['original_units'] for i in items)>1000000000 or sum(cents(Decimal(i['data']['unit_cost_rub'])*i['data']['original_units']) for i in items)>100000000000:error(items[0],'Общий артикул: не более 1 млрд единиц и 1 млрд рублей исходной стоимости')
   if len({i['data']['cutover_date'] for i in items})!=1:error(items[0],'У артикула должна быть одна дата перехода')
   totals=defaultdict(int)
   for i in items:totals[i['data']['chrt_id']]+=i['data']['remaining_units'] if i['data']['location']=='wb' else 0
   if stocks:
    actual=defaultdict(int)
    for s in stocks:actual[int(s['chrt_id'] or 0)]+=int(s['quantity'] or 0)
    if dict(actual)!=dict(totals):error(items[0],'Остаток WB должен точно совпадать с текущим snapshot; таблица WB не заменяется')
 else:
  try:plans=simulate(c,rows,state)
  except ValueError as e:error(rows[0],e)
 fingerprint=digest({'kind':kind,'rows':rows,'errors':errors,'state':state,'cards':cards,'plans':plans})
 return {'kind':kind,'rows':rows,'errors':errors,'valid_count':len(rows),'can_confirm':bool(rows) and not errors,'preview_fingerprint':fingerprint,'manual_source':True,'physical_replay':False,'_plans':plans}

def simulate(c,rows,state):
 lots={r['source_id']:r for r in state['migration_finished_lots']};groups=defaultdict(list);rrds=set();used=set();plans=[]
 olddocs={r['document_id'] for r in state['migration_historical_documents']}
 oldlots={r['lot_source_id'] for r in state['migration_historical_allocations']}
 for item in rows:groups[item['data']['document_id']].append(item['data'])
 balances={};sales={};sequences=set();returned=defaultdict(lambda:[0,0])
 for doc,group in sorted(groups.items(),key=lambda p:(p[1][0]['operation_date'],p[1][0]['sequence'],p[0])):
  r=group[0];header={k:v for k,v in r.items() if k not in ('source_id','lot_source_id','allocation_units')}
  if doc in olddocs or r['rrd_id'] in rrds or (exists(c,'financial_report') and c.execute('SELECT 1 FROM financial_report WHERE rrd_id=?',(r['rrd_id'],)).fetchone()):raise ValueError('Документ/финансовый ID уже существует: источник WB не перезаписывается')
  rrds.add(r['rrd_id'])
  if (r['operation_date'],r['sequence']) in sequences:raise ValueError('Порядок документов в одной дате должен быть явным и уникальным')
  sequences.add((r['operation_date'],r['sequence']))
  if any({k:v for k,v in a.items() if k not in ('source_id','lot_source_id','allocation_units')}!=header for a in group):raise ValueError('Все строки документа должны повторять одинаковую финансовую шапку')
  if sum(a['allocation_units'] for a in group)!=r['quantity']:raise ValueError('Сумма allocations должна равняться количеству документа')
  if len({a['lot_source_id'] for a in group})!=len(group):raise ValueError('Партия повторяется в документе')
  allocations=[]
  for a in group:
   lot=lots.get(a['lot_source_id'])
   if not lot or a['lot_source_id'] in oldlots:raise ValueError('Партия неизвестна или её полная история уже перенесена')
   source=json.loads(lot['snapshot_json'])
   if (a['nm_id'],a['chrt_id'])!=(lot['nm_id'],lot['chrt_id']) or not source['source_date']<=a['operation_date']<lot['cutover_date']:raise ValueError('Артикул/вариант/дата документа не совпадает с партией до перехода')
   used.add(a['lot_source_id']);balance=balances.setdefault(a['lot_source_id'],[lot['original_units'],lot['original_cents']]);qty=a['allocation_units']
   if a['event_type']=='sale':
    if qty>balance[0]:raise ValueError('Историческая продажа превышает доступную партию')
    amount=part(balance[1],qty,balance[0]);balance[0]-=qty;balance[1]-=amount;sales[(doc,a['lot_source_id'])]=(qty,amount)
   else:
    original=sales.get((a['original_sale_document'],a['lot_source_id']))
    if not original:raise ValueError('Нет исходной продажи этой партии в том же полном CSV')
    counter=returned[(a['original_sale_document'],a['lot_source_id'])]
    if counter[0]+qty>original[0]:raise ValueError('Возврат превышает исходную продажу')
    cumulative=part(original[1],counter[0]+qty,original[0]);amount=cumulative-counter[1];counter[:]=[counter[0]+qty,cumulative];balance[0]+=qty;balance[1]+=amount
   allocations.append({'source_id':a['source_id'],'lot_source_id':a['lot_source_id'],'units':qty,'cost_cents':amount,'original_sale_document':a['original_sale_document'],'snapshot_json':dump(a)})
  plans.append({'header':header,'allocations':allocations,'cogs_cents':sum(a['cost_cents'] for a in allocations)})
 for key in used:
  if balances[key]!=[lots[key]['remaining_units'],lots[key]['remaining_cents']]:raise ValueError('Полная история партии должна точно сходиться с остатком и копейками на дату перехода')
 return plans

def apply(c,kind,rows,plans):
 schema(c)
 if kind=='finished_lots':
  from .fifo_finished_goods import _create_finished_goods_layer
  from .production import _apply_pipeline_delta
  for item in rows:
   r=item['data'];card=validate_catalog_identity(c,r['nm_id'],r['chrt_id'],True)
   original=cents(Decimal(r['unit_cost_rub'])*r['original_units']);remaining=cents(r['remaining_cost_rub'])
   c.execute('INSERT INTO migration_finished_lots VALUES(?,0,?,?,?,?,?,?,?,NULL,?)',(r['source_id'],r['nm_id'],r['chrt_id'],r['cutover_date'],r['original_units'],r['remaining_units'],original,remaining,dump(r)))
   if r['remaining_units']:
    rate=float(Decimal(remaining)/100/r['remaining_units'])
    layer=_create_finished_goods_layer(c,r['nm_id'],card['supplier_article'],card['product_name'],'migration_finished_opening','csv:0:'+r['source_id'],r['cutover_date'],r['remaining_units'],rate,r['location'],r['document_proof'],r['chrt_id'])
    c.execute('UPDATE finished_goods_cost_layers SET original_amount_rub=? WHERE id=?',(remaining/100,layer))
    # Immutable archive row already inserted; layer provenance linked through immutable unique source_ref.
    if r['location']!='wb':_apply_pipeline_delta(c,r['nm_id'],card['supplier_article'],r['remaining_units'] if r['location']=='ready' else 0,r['remaining_units'] if r['location']=='inbound' else 0)
 else:
  from .financial_source import record,fingerprint
  for plan in plans:
   r=plan['header'];card=validate_catalog_identity(c,r['nm_id'],r['chrt_id'],True)
   values={'rrd_id':r['rrd_id'],'operation_date':r['operation_date'],'sale_date':r['operation_date'],'nm_id':r['nm_id'],'supplier_article':card['supplier_article'],'quantity':r['quantity'],'retail_price_withdisc_rub':float(abs(Decimal(r['retail_amount']))/r['quantity']),'operation_name':'Продажа' if r['event_type']=='sale' else 'Возврат','doc_type_name':'Продажа' if r['event_type']=='sale' else 'Возврат','raw_json':dump({'migration_shop_id':0,'migration_document':r,'chrt_id':r['chrt_id'],'srid':'migration:0:'+r['document_id']}),**{m:float(r[m]) for m in MONEY},'ppvz_sales_commission':float(r['commission'])}
   c.execute('INSERT INTO financial_report('+','.join(values)+') VALUES('+','.join('?' for _ in values)+')',tuple(values.values()))
   actual=dict(c.execute('SELECT * FROM financial_report WHERE rrd_id=?',(r['rrd_id'],)).fetchone());record(c,[actual],None)
   c.execute('INSERT INTO migration_historical_documents VALUES(?,?,0,?,?,?,?,?,?,?,?)',(r['document_id'],r['rrd_id'],r['nm_id'],r['chrt_id'],r['operation_date'],r['event_type'],r['quantity'],plan['cogs_cents'],fingerprint(actual),dump(r)))
   for a in plan['allocations']:c.execute('INSERT INTO migration_historical_allocations VALUES(?,?,?,?,?,?,?)',(a['source_id'],r['document_id'],a['lot_source_id'],a['units'],a['cost_cents'],a['original_sale_document'],a['snapshot_json']))

def project(c,financial_rows,rows):
 if not exists(c,'migration_historical_documents'):return rows
 from .financial_source import fingerprint
 facts={r['rrd_id']:r for r in financial_rows}
 for row in rows:
  archived=c.execute('SELECT * FROM migration_historical_documents WHERE rrd_id=?',(row['rrd_id'],)).fetchone()
  if not archived:continue
  fact=facts.get(row['rrd_id'])
  if not fact or fingerprint(fact)!=archived['financial_fingerprint']:
   row.update(status='insufficient',reason='migration_historical_source_changed',eligible_fifo_cogs_rub=None)
  else:row.update(status='manual_source',reason='migration_manual_source',eligible_fifo_cogs_rub=archived['cogs_cents']/100*(-1 if archived['event_type']=='return' else 1),matched_units=archived['quantity'],layer_sources=['manual_csv_archive'])
 return rows


def physical_amount(c,layer_id,location,quantity):
 """Imported physical basis in integer cents, including active moves/returns/reversals."""
 from .imported_shared_basis import location_amount
 central=location_amount(c,layer_id,location,quantity)
 if central is not None:return central
 layer=c.execute('SELECT * FROM finished_goods_cost_layers WHERE id=?',(layer_id,)).fetchone()
 if not layer or layer['source_type']!='migration_finished_opening':return None
 lot=c.execute('SELECT * FROM migration_finished_lots WHERE source_id=?',(layer['source_ref'][len('csv:0:'):],)).fetchone()
 if not lot:raise ValueError('CSV physical provenance missing')
 original=json.loads(lot['snapshot_json']);units={loc:0 for loc in ('ready','inbound','wb')};money={loc:0 for loc in units}
 units[original['location']]=lot['remaining_units'];money[original['location']]=lot['remaining_cents']
 for a in c.execute("SELECT * FROM finished_goods_fifo_allocations WHERE layer_id=? AND status='applied' AND reversed_at IS NULL ORDER BY id",(layer_id,)):
  amount=cents(a['amount_rub'])
  if a['from_location'] in units:units[a['from_location']]-=a['units'];money[a['from_location']]-=amount
  if a['to_location'] in units:units[a['to_location']]+=a['units'];money[a['to_location']]+=amount
 if any(units[loc]!=layer[loc+'_units'] or money[loc]<0 or units[loc]<0 for loc in units):raise ValueError('CSV physical quantity/money lineage does not reconcile')
 if quantity>units[location]:raise ValueError('CSV physical shortage')
 return part(money[location],quantity,units[location])/100


def central_setup_guard(c,nm_id):
 from .imported_shared_basis import sku_transferred
 if sku_transferred(c,nm_id):return
 if not exists(c,'migration_finished_lots'):return
 lots=c.execute('SELECT * FROM migration_finished_lots WHERE nm_id=?',(nm_id,)).fetchall()
 if not lots:return
 raise ValueError('Перенесённые CSV партии используют локальный FIFO; переход в общий склад требует отдельного переноса денежной основы')


def operation_guard(c,nm_id,day):
 boundary=cutoff(c,nm_id)
 if boundary:
  try:actual=date.fromisoformat(str(day)[:10]).isoformat()
  except ValueError:raise ValueError('CSV cutover: физическая операция требует явной даты')
  if actual<boundary:raise ValueError('CSV cutover: физическая операция не может предшествовать начальному остатку')


def register_valuation(c):
 """Read-only exact imported residual; legacy layers retain their original valuation."""
 if not c.in_transaction:c.execute('BEGIN')
 values={}
 if exists(c,'migration_finished_lots'):
  for layer in c.execute("SELECT * FROM finished_goods_cost_layers WHERE source_type='migration_finished_opening' AND status<>'reversed'").fetchall():
   for location in ('ready','inbound','wb'):
    qty=layer[location+'_units']
    values[(layer['id'],location)]=physical_amount(c,layer['id'],location,qty) if qty else 0.0
 c.create_function('finished_location_amount',4,lambda lid,loc,qty,rate:values.get((lid,loc),float(qty or 0)*float(rate or 0)))
