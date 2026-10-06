"""Bounded local Ozon cabinet reads; no upstream requests or guessed sales."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import json

from db import core
from db.ozon_accounting import ensure_schema, _collection_verified, source_seller_matches
import ozon_insights as insights

MAX_ROWS = 20000
MAX_BYTES = 16 * 1024 * 1024
MAX_ITEMS = 500


def _source(start, end, current=False):
    insights.window(start, end)
    with core.connect() as conn:
        ensure_schema(conn)
        row = conn.execute('SELECT start_date,end_date,created_at,CASE WHEN length(CAST(payload_json AS BLOB))<=? THEN payload_json END FROM ozon_accounting_snapshots ' + ('ORDER BY created_at DESC,period_key DESC LIMIT 1' if current else 'WHERE period_key=?'), (MAX_BYTES,) if current else (MAX_BYTES,start+':'+end)).fetchone()
        stale = bool(row and not current and conn.execute('SELECT 1 FROM ozon_accounting_snapshots WHERE start_date<=? AND end_date>=? AND created_at>? LIMIT 1',(end,start,row[2])).fetchone())
    if row and row[3] is None: raise ValueError('Источник Ozon превышает безопасный объём.')
    data = json.loads(row[3]) if row and not stale else None
    if data is not None and not isinstance(data,dict):raise ValueError('Неизвестная структура источника Ozon.')
    if data is not None and not isinstance(data.get('source_endpoints',{}),dict):raise ValueError('Неизвестная структура версий источника Ozon.')
    identity_mismatch=data is not None and not source_seller_matches(data)
    if identity_mismatch:data=None
    verified = bool(data and _collection_verified(data,row[0],row[1]))
    result = {'marketplace':'ozon','loaded':data is not None,'synced_at':row[2] if row else None,
              'period':{'start_date':start,'end_date':end},'items':[],'metrics':{},'blockers':[],
              'coverage':{'source_collection_complete':verified,'source_snapshot_stale':stale,
                          'record_parse_complete':data is not None,'marketplace_complete':False,
                          'source_identity_confirmed':data is not None,
                          'scope':'current_snapshot' if current else 'selected_period'},'truncated':False}
    if row: result['source_period']={'start_date':row[0],'end_date':row[1]}
    if data is None: result['blockers'].append('Продавец источника Ozon не подтверждён для текущего подключения; загрузите период заново.' if identity_mismatch else 'Источник Ozon устарел; загрузите период повторно.' if stale else 'Источник Ozon ещё не загружен.')
    elif not verified: result['blockers'].append('Полнота загрузки источника не подтверждена.')
    return result,data


def _rows(data,key):
    rows=data.get(key)
    if not isinstance(rows,list) or len(rows)>MAX_ROWS or any(not isinstance(r,dict) for r in rows):
        raise ValueError('Неизвестная или слишком большая коллекция Ozon: '+key)
    return rows


def _units(value):
    return value if type(value) is int and 0<=value<=1000000000 else None


def _text(value):
    return value if isinstance(value,str) and 0<len(value)<=1000 else None


def _money(value):
    if not isinstance(value,dict) or value.get('currency')!='RUB' or isinstance(value.get('amount'),bool): return None
    try:
        amount=Decimal(str(value['amount']))
        if not amount.is_finite() or amount<0 or amount>Decimal('1000000000000'): return None
        return insights._amount(amount)
    except (KeyError,InvalidOperation,ValueError): return None


def _dedup(rows,identity,result):
    seen={}; conflicts=set()
    for row in rows:
        key=identity(row)
        if not key:
            result['coverage']['record_parse_complete']=False; continue
        encoded=json.dumps(row,sort_keys=True,ensure_ascii=False)
        if key in seen and seen[key][0]!=encoded: conflicts.add(key)
        else: seen[key]=(encoded,row)
    if conflicts:
        result['coverage']['record_parse_complete']=False
        result['blockers'].append('Конфликтующие идентификаторы источника исключены из итогов.')
    return [value[1] for key,value in seen.items() if key not in conflicts]


def _finish(result,items):
    result['truncated']=len(items)>MAX_ITEMS
    result['items']=items[:MAX_ITEMS]
    if not result['coverage']['record_parse_complete']: result['blockers'].append('Часть строк источника не подтверждена; итоги неизвестны.')
    return result


def orders(start,end,kind='all'):
    if kind not in {'all','fbs','fbo'}: raise ValueError('Неизвестный тип отправлений Ozon.')
    result,data=_source(start,end); items=[]; total=0
    result['kind']=kind; result['metrics']={'posting_count':None,'order_units':None}
    if data is None:return result
    raw=[]
    for channel in ('fbo','fbs'):
        for row in _rows(data,channel): raw.append({**row,'_kind':channel})
    if len(raw)>MAX_ROWS:raise ValueError('Слишком много отправлений Ozon.')
    child_count=0
    for row in _dedup(raw,lambda r:_text(r.get('posting_number')),result):
        channel=row['_kind']
        if kind!='all' and kind!=channel:continue
        current_fbs=channel=='fbs' and data.get('source_endpoints',{}).get('fbs')=='/v4/posting/fbs/list'
        stamp=row.get('in_process_at') if channel=='fbs' and (current_fbs or row.get('in_process_at')) else row.get('created_at')
        basis='in_process_at' if channel=='fbs' and row.get('in_process_at') else 'created_at'
        try:
            when=datetime.fromisoformat(stamp.replace('Z','+00:00'))
            if when.tzinfo is None:raise ValueError()
            if not start<=when.astimezone(timezone.utc).date().isoformat()<=end:continue
        except (ValueError,AttributeError):result['coverage']['record_parse_complete']=False;continue
        products=row.get('products'); prepared=[]; quantity=0
        if not isinstance(products,list) or not products or len(products)>MAX_ROWS:
            result['coverage']['record_parse_complete']=False;continue
        child_count+=len(products)
        if child_count>MAX_ROWS:raise ValueError('Слишком много товарных строк отправлений Ozon.')
        for product in products:
            if not isinstance(product,dict):result['coverage']['record_parse_complete']=False;continue
            units=_units(product.get('quantity'));sku=insights._valid_sku(product.get('sku'))
            if units is None or sku is None:result['coverage']['record_parse_complete']=False
            else:quantity+=units
            prepared.append({'sku':sku,'offer_id':_text(product.get('offer_id')),'name':_text(product.get('name')),'quantity':units,'price_rub':_money(product.get('price'))})
        total+=quantity
        items.append({'posting_number':row['posting_number'],'kind':channel,'status':_text(row.get('status')),
                      'created_at':stamp,'date_basis':basis,'quantity_units':quantity if prepared and len(prepared)==len(products) and all(p['quantity'] is not None and p['sku'] for p in prepared) else None,
                      'products':prepared,'price_rub':None})
    if result['coverage']['record_parse_complete'] and (items or result['coverage']['source_collection_complete']):result['metrics']={'posting_count':len(items),'order_units':total}
    result['note']='Отправления, включая отменённые, не равны продажам. Цена товара справочная; сумма отправления не рассчитывается.'
    return _finish(result,items)


def stock(start,end):
    result,data=_source(start,end,True);result['metrics']={'present_units':None,'reserved_units':None}
    if data is None:return result
    raw=[]
    for product in _rows(data,'stocks'):
        children=product.get('stocks')
        if not isinstance(children,list):result['coverage']['record_parse_complete']=False;continue
        for child in children:
            if not isinstance(child,dict):result['coverage']['record_parse_complete']=False;continue
            raw.append({**child,'offer_id':product.get('offer_id'),'product_id':product.get('product_id')})
            if len(raw)>MAX_ROWS:raise ValueError('Слишком много строк остатков Ozon.')
    items=[]
    for row in _dedup(raw,lambda r:(insights._valid_sku(r.get('sku')),_text(r.get('type')),_text(r.get('shipment_type'))) if insights._valid_sku(r.get('sku')) else None,result):
        present,reserved=_units(row.get('present')),_units(row.get('reserved'))
        if present is None or reserved is None:result['coverage']['record_parse_complete']=False
        items.append({'sku':insights._valid_sku(row.get('sku')),'offer_id':_text(row.get('offer_id')) or '',
                      'product_id':insights._valid_sku(row.get('product_id')),'source':_text(row.get('type')),
                      'warehouse_id':None,'shipment_type':_text(row.get('shipment_type')),'present_units':present,'reserved_units':reserved})
    if result['coverage']['record_parse_complete'] and (items or result['coverage']['source_collection_complete']):result['metrics']={'present_units':sum(i['present_units'] for i in items),'reserved_units':sum(i['reserved_units'] for i in items)}
    result['note']='Текущий снимок остатков, не история выбранного периода. Источник не содержит идентификатора склада.'
    return _finish(result,items)


def returns(start,end):
    result,data=_source(start,end,True);result['coverage']['scope']='current_registry'
    if data is None:return result
    items=[]
    for row in _dedup(_rows(data,'returns'),lambda r:insights._valid_sku(r.get('id')),result):
        product=row.get('product') or {};visual=row.get('visual') or {};logistic=row.get('logistic') or {}
        if not all(isinstance(v,dict) for v in (product,visual,logistic)):result['coverage']['record_parse_complete']=False;continue
        status=visual.get('status') or {}
        if not isinstance(status,dict):status={}
        sku,units=insights._valid_sku(product.get('sku')),_units(product.get('quantity'))
        if sku is None or units is None:result['coverage']['record_parse_complete']=False
        items.append({'id':str(row['id']),'posting_number':_text(row.get('posting_number')),'sku':sku,'quantity':units,
                      'status':_text(status.get('display_name')) or _text(status.get('sys_name')),
                      'status_code':_text(status.get('sys_name')),'created_at':_text(logistic.get('return_date')),
                      'date_basis':'logistic.return_date','status_changed_at':_text(visual.get('change_moment'))})
    result['note']='Текущий реестр возвратов без фильтра периода; возвраты не преобразуются в подтверждённые продажи или выплаты.'
    return _finish(result,items)


def overview(start,end):
    result=orders(start,end);result.pop('items',None);result.pop('kind',None)
    report=insights.monthly(start,end);loaded=[r for r in report['items'] if r['document_lines']]
    result['metrics']={'order_units':result['metrics']['order_units'],'document_revenue_rub':None,'document_profit_rub':None,'unknown_cost_units':None}
    if loaded:
        result['metrics']['document_revenue_rub']=insights._amount(sum(Decimal(r['revenue_rub']) for r in loaded))
        result['metrics']['unknown_cost_units']=sum(r['unknown_cost_units'] for r in loaded)
        if all(r['result_after_fifo_rub'] is not None for r in loaded):result['metrics']['document_profit_rub']=insights._amount(sum(Decimal(r['result_after_fifo_rub']) for r in loaded))
    result['blockers']=list(dict.fromkeys(result['blockers']+report['blockers']))
    result['document_coverage']=report['coverage'];result['note']='Документальный результат только по загруженным документам; полнота маркетплейса не подтверждена.'
    return result


def control(start,end):
    result,data=_source(start,end)
    result['items']=[{'key':'source','label':'Источник Ozon','status':'known' if result['coverage']['source_collection_complete'] else 'unknown','note':'Полнота сбора не означает полноту финансового результата.'},
                     {'key':'external_actions','label':'Внешние операции','status':'unknown','note':'Этот раздел читает локальные снимки; не меняет заказы, цены или поставки Ozon.'}]
    return result
