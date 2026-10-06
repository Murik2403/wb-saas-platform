"""Explicit Ozon price drafts and idempotent, single-attempt Seller writes."""
from __future__ import annotations
import csv, hashlib, io, json, re, sqlite3, time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import requests
import config
from ozon_accounting_client import OzonAccountingClient, OzonAccountingError

class PriceError(ValueError):
    def __init__(self,message,code='invalid',status=422):super().__init__(message);self.code=code;self.status=status

def fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()).hexdigest()
def identity():
    creds=config.get_ozon_credentials()
    if not creds:raise PriceError('Подключите Ozon Seller API.','not_connected',409)
    return fingerprint({'seller_client_id':str(creds[0])}),creds

def connection():
    db=sqlite3.connect(config.DB_PATH,timeout=5)
    db.row_factory=sqlite3.Row
    db.executescript('CREATE TABLE IF NOT EXISTS ozon_price_snapshot (account TEXT PRIMARY KEY,payload TEXT NOT NULL); CREATE TABLE IF NOT EXISTS ozon_price_operation (request_key TEXT PRIMARY KEY,account TEXT NOT NULL,draft_hash TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL);')
    if 'actor' not in {row[1] for row in db.execute('PRAGMA table_info(ozon_price_operation)')}:
        db.execute("ALTER TABLE ozon_price_operation ADD COLUMN actor TEXT NOT NULL DEFAULT ''")
        db.commit()
    if 'created_at' not in {row[1] for row in db.execute('PRAGMA table_info(ozon_price_operation)')}:
        # Old rows get 0, i.e. "older than any TTL": a stuck in_progress row from before the upgrade is released.
        db.execute("ALTER TABLE ozon_price_operation ADD COLUMN created_at REAL NOT NULL DEFAULT 0")
        db.commit()
    return db

PENDING_TTL_SECONDS=600
MAX_PRICE_CHANGE=Decimal('0.70')

def large_changes(items):
    """product_ids whose new price deviates from the previous one by more than MAX_PRICE_CHANGE."""
    out=[]
    for r in items:
        try:old=Decimal(str(r['previous_price']))
        except (InvalidOperation,TypeError,ValueError):continue
        if old>0 and abs(Decimal(r['price'])-old)/old>MAX_PRICE_CHANGE:out.append(r['product_id'])
    return out

def money(value):
    if isinstance(value,(bool,float)) or value is None:raise PriceError('Цена должна быть точной десятичной строкой RUB.')
    try:n=Decimal(str(value))
    except InvalidOperation:raise PriceError('Некорректная цена RUB.') from None
    if not n.is_finite() or n<=0 or n>Decimal('1000000000') or n!=n.quantize(Decimal('.01')):raise PriceError('Цена должна быть больше нуля, не выше 1 млрд и с точностью до копейки.')
    return format(n,'.2f')

class PriceClient(OzonAccountingClient):
    def __init__(self,*args,**kwargs):super().__init__(*args,max_pages=20,max_rows=20000,**kwargs);self.deadline=time.monotonic()+30
    def _post(self,path,body,**kwargs):return super()._post(path,body,timeout=5)
    def prices(self):
        raw=self._pages('/v5/product/info/prices',{'filter':{'visibility':'ALL'},'limit':1000},rows='items',cursor='cursor')
        result=[];seen=set()
        for row in raw:
            pid=row.get('product_id');offer=row.get('offer_id');price=row.get('price')
            if isinstance(pid,bool) or not str(pid).isdigit() or int(pid)<=0 or not isinstance(offer,str) or not isinstance(price,dict):raise PriceError('Не распознана строка цен Ozon. Снимок не заменён.')
            if str(pid) in seen:raise PriceError('Повтор идентификатора товара в ценах Ozon.')
            seen.add(str(pid));currency=price.get('currency_code')
            result.append({'product_id':str(pid),'offer_id':offer,'currency_code':currency,'price':money(price.get('price')) if currency=='RUB' else None,'old_price':str(price.get('old_price')) if price.get('old_price') is not None else None,'min_price':str(price.get('min_price')) if price.get('min_price') is not None else None})
        return sorted(result,key=lambda r:r['product_id'])
    def send_once(self,rows):
        response=None
        try:
            response=self.session.post(self.BASE+'/v1/product/import/prices',headers=self.headers,json={'prices':rows},timeout=5,allow_redirects=False,stream=True)
            if response.status_code==429:raise PriceError('Ozon ограничил частоту. Повторная отправка этой операции запрещена; обновите цены и создайте новый черновик.','rate_limit',429)
            if response.status_code!=200:raise PriceError('Ozon не подтвердил результат операции. Проверьте реальные цены перед новым черновиком.','unknown_outcome',409)
            raw=bytearray();deadline=time.monotonic()+8
            for chunk in response.iter_content(65536):
                if len(raw)+len(chunk)>2*1024*1024 or time.monotonic()>deadline:raise PriceError('Ответ об изменении цен не подтверждён.','unknown_outcome',409)
                raw.extend(chunk)
            value=json.loads(raw.decode('utf-8'),parse_float=str)
            return value
        except requests.RequestException:raise PriceError('Ответ Ozon потерян; цена могла измениться. Проверьте источник. Автоповтора нет.','unknown_outcome',409) from None
        except (ValueError,UnicodeError) as e:
            if isinstance(e,PriceError):raise
            raise PriceError('Ответ об изменении цен не распознан; автоматического повтора нет.','unknown_outcome',409) from None
        finally:
            if response is not None:response.close()

def client(creds):return PriceClient(*creds)
def read():
    try:account,_=identity()
    except PriceError:return {'marketplace':'ozon','configured':False,'loaded':False,'items':[],'source_fingerprint':None,'synced_at':None}
    with connection() as db:row=db.execute('SELECT payload FROM ozon_price_snapshot WHERE account=?',(account,)).fetchone()
    return json.loads(row['payload']) if row else {'marketplace':'ozon','configured':True,'loaded':False,'items':[],'source_fingerprint':None,'synced_at':None}

def refresh():
    account,creds=identity()
    try:rows=client(creds).prices()
    except OzonAccountingError as e:raise PriceError(str(e),'source_read',429 if getattr(e,'status_code',None)==429 else 422) from None
    if identity()[0]!=account:raise PriceError('Подключение Ozon сменилось. Снимок не сохранён.','stale',409)
    result={'marketplace':'ozon','configured':True,'loaded':True,'items':rows,'source_fingerprint':fingerprint({'account':account,'rows':rows}),'synced_at':datetime.now(timezone.utc).isoformat(),'complete':True}
    with connection() as db:db.execute('INSERT OR REPLACE INTO ozon_price_snapshot VALUES (?,?)',(account,json.dumps(result)))
    return result

def preview(rows,source_fingerprint):
    current=read()
    if not current['loaded'] or source_fingerprint!=current['source_fingerprint']:raise PriceError('Снимок цен устарел. Обновите источник и черновик.','stale',409)
    if not isinstance(rows,list) or not 1<=len(rows)<=1000:raise PriceError('Выберите от 1 до 1000 товаров.')
    sources={r['product_id']:r for r in current['items']};out=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'product_id','price'}:raise PriceError('Черновик содержит неподтверждённые поля.')
        pid=str(row['product_id']);source=sources.get(pid)
        if not source or pid in seen or source['currency_code']!='RUB':raise PriceError('Товар не найден, повторён или валюта не RUB.')
        seen.add(pid);out.append({'product_id':pid,'offer_id':source['offer_id'],'previous_price':source['price'],'price':money(row['price']),'currency_code':'RUB'})
    return {'items':sorted(out,key=lambda r:r['product_id']),'source_fingerprint':source_fingerprint,'draft_fingerprint':fingerprint(sorted(out,key=lambda r:r['product_id'])),'large_changes':large_changes(out),'external_write':False}

def confirm(rows,source_fingerprint,request_key,confirmed,actor='',allow_large_change=False):
    if confirmed is not True:raise PriceError('Подтвердите изменение цен в Ozon.')
    if not isinstance(request_key,str) or not re.fullmatch(r'[A-Za-z0-9_-]{16,128}',request_key):raise PriceError('Укажите уникальный ключ операции.')
    account,creds=identity();draft_hash=fingerprint({'rows':rows,'source':source_fingerprint})
    with connection() as db:
        db.execute('BEGIN IMMEDIATE')
        # A process killed mid-send leaves in_progress forever: past the TTL the outcome is unknown, not pending.
        for stale in db.execute("SELECT request_key,payload FROM ozon_price_operation WHERE status='in_progress' AND created_at<?",(time.time()-PENDING_TTL_SECONDS,)).fetchall():
            db.execute("UPDATE ozon_price_operation SET status='unknown_outcome',payload=? WHERE request_key=?",(json.dumps({**json.loads(stale['payload']),'status':'unknown_outcome'}),stale['request_key']))
        existing=db.execute('SELECT * FROM ozon_price_operation WHERE request_key=?',(request_key,)).fetchone()
        if existing:
            if existing['account']!=account or existing['draft_hash']!=draft_hash:raise PriceError('Ключ операции уже использован для другого черновика.','idempotency_conflict',409)
            return json.loads(existing['payload'])
        if db.execute("SELECT 1 FROM ozon_price_operation WHERE account=? AND status='in_progress'",(account,)).fetchone():
            raise PriceError('Другая отправка цен ещё не завершена. Проверьте её результат; новая отправка заблокирована.','operation_pending',409)
        checked=preview(rows,source_fingerprint)
        if checked['large_changes'] and allow_large_change is not True:raise PriceError('Цена меняется больше чем на 70%. Подтвердите большое изменение.','price_change_too_large',422)
        initial={'request_key':request_key,'status':'in_progress','items':[],'automatic_retry':False}
        db.execute('INSERT INTO ozon_price_operation (request_key,account,draft_hash,status,payload,actor,created_at) VALUES (?,?,?,?,?,?,?)',(request_key,account,draft_hash,'in_progress',json.dumps(initial),str(actor)[:320],time.time()));db.commit()
    outcome={'request_key':request_key,'status':'unknown_outcome','items':[],'automatic_retry':False}
    try:
        transport=client(creds);latest=transport.prices()
        if fingerprint({'account':account,'rows':latest})!=source_fingerprint or identity()[0]!=account:raise PriceError('Цены или подключение Ozon изменились. Отправка отменена.','stale',409)
        outgoing=[{'product_id':int(r['product_id']),'price':r['price'],'currency_code':'RUB'} for r in checked['items']]
        result=transport.send_once(outgoing)
        raw=result.get('result') if isinstance(result,dict) else None
        if not isinstance(raw,list):raise PriceError('Ozon не подтвердил строки обновления.','unknown_outcome',409)
        expected={r['product_id'] for r in checked['items']};seen=set();parsed=[]
        for row in raw:
            if not isinstance(row,dict):raise PriceError('Не распознаны результаты Ozon.','unknown_outcome',409)
            pid=str(row.get('product_id'))
            if pid not in expected or pid in seen or type(row.get('updated')) is not bool or not isinstance(row.get('errors'),list):raise PriceError('Неполные результаты обновления цен Ozon.','unknown_outcome',409)
            seen.add(pid);parsed.append({'product_id':pid,'updated':row['updated'] and not row['errors'],'errors':[{'code':str(e.get('code','unknown'))} if isinstance(e,dict) else {'code':'unknown'} for e in row['errors']]})
        if seen!=expected:raise PriceError('Не все товары подтверждены Ozon.','unknown_outcome',409)
        outcome.update(status='completed' if all(r['updated'] for r in parsed) else 'partial',items=parsed)
        with connection() as db:db.execute('DELETE FROM ozon_price_snapshot WHERE account=?',(account,))
    except (PriceError,OzonAccountingError) as e:
        outcome.update(status=getattr(e,'code','source_read'),message=str(e))
    finally:
        with connection() as db:db.execute('UPDATE ozon_price_operation SET status=?,payload=? WHERE request_key=?',(outcome['status'],json.dumps(outcome),request_key))
    return outcome

def export_csv():
    output=io.StringIO();writer=csv.writer(output,delimiter=';');writer.writerow(['product_id','offer_id','price_RUB','currency_code'])
    def cell(value):
        text='' if value is None else str(value)
        return "'"+text if text.lstrip().startswith(('=','+','-','@')) else text
    for r in read()['items']:writer.writerow([cell(r[key]) for key in ('product_id','offer_id','price','currency_code')])
    return output.getvalue()
