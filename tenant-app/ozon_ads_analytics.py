"""Dedicated Performance API reporting, isolated from Ozon Seller credentials.

Remote writes are limited to OAuth and report generation, never campaign/budget
changes. Saved analytics are published atomically only after a complete report.
CSV mapping follows Ozon's public CPC report example; missing cells stay unknown.

Primary contracts: https://docs.ozon.ru/api/performance/ (Performance 2.0,
SubmitRequest, StatisticsCheck, DownloadStatistics; inspected 2026-10-07).
Public example: https://cdn.ozone.ru/s3/ozon-disk-api/techdoc/performance/api/
report-examples/stencils16-02_1770993970.zip . Real-account report acceptance is
separate from these contract tests; no real Performance key is bundled.
"""
from __future__ import annotations
import csv, hashlib, io, json, math, re, sqlite3, time, uuid, zipfile
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
import requests
import config
import secure_storage
from db.core import connect

MAX_REPORT_BYTES=8*1024*1024
MAX_ROWS=20000
MAX_POLLS=30
PERFORMANCE_URL='https://api-performance.ozon.ru'
METRICS=('views','clicks','spend','orders','ad_revenue','model_orders','model_revenue','carts')
HEADERS={'views':'Показы','clicks':'Клики','spend':'Расход','orders':'Заказы','ad_revenue':'Продажи','model_orders':'Заказы модели','model_revenue':'Продажи с заказов модели','carts':'В корзину'}
COUNTS={'views','clicks','orders','model_orders','carts'}

class PerformanceError(ValueError):
    def __init__(self,code,message,retryable=False):
        super().__init__(message);self.code=code;self.retryable=retryable

def period(start,end):
    start=date.fromisoformat(str(start));end=date.fromisoformat(str(end))
    if end<start or (end-start).days>=62:raise ValueError('Период рекламы: от 1 до 62 дней.')
    return start.isoformat(),end.isoformat()

def credential_path():return config.DATA_DIR/'.ozon_performance_credentials'
def credentials():
    value,legacy=secure_storage.read(credential_path(),config.DATA_ENCRYPTION_KEY)
    if legacy:raise PerformanceError('credential_format','Ключ Performance должен храниться зашифрованно.')
    if not value:return None
    try:
        obj=json.loads(value)
        if not isinstance(obj,dict) or set(obj)!={'client_id','client_secret'} or not all(isinstance(v,str) and v.strip() for v in obj.values()):raise ValueError()
        return obj
    except (ValueError,TypeError):raise PerformanceError('credential_format','Проверьте подключение Performance API.') from None

def save_credentials(client_id,client_secret):
    values={'client_id':str(client_id).strip(),'client_secret':str(client_secret).strip()}
    if not all(values.values()) or any(len(v)>2048 or any(ord(c)<32 for c in v) for v in values.values()):raise ValueError('Укажите отдельные Client ID и Client Secret Performance API.')
    old=credentials()
    secure_storage.write(credential_path(),json.dumps(values,separators=(',',':')),config.DATA_ENCRYPTION_KEY)
    if old!=values:
        with connect() as conn:
            schema(conn);conn.execute('DELETE FROM ozon_ads_snapshots');conn.execute("UPDATE ozon_ads_reports SET status='cancelled',error_code='credentials_changed' WHERE status='pending'")
    return {'configured':True}

def delete_credentials():
    credential_path().unlink(missing_ok=True)
    with connect() as conn:
        schema(conn);conn.execute('DELETE FROM ozon_ads_snapshots');conn.execute("UPDATE ozon_ads_reports SET status='cancelled',error_code='credentials_removed' WHERE status='pending'")
def credential_fingerprint(value):return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()

class PerformanceClient:
    """At most two HTTP attempts per call, no sleep and no redirect credentials."""
    def __init__(self,client_id,client_secret,session=None,timeout=5,clock=time.monotonic):
        self.client_id=client_id;self.client_secret=client_secret;self.session=session or requests.Session();self.timeout=timeout;self.clock=clock;self.token=None;self.expires=0
    def _raw(self,method,path,**kwargs):
        kwargs.setdefault('stream',True)
        try:
            response=self.session.request(method,PERFORMANCE_URL+path,timeout=self.timeout,allow_redirects=False,**kwargs)
        except requests.RequestException:raise PerformanceError('network','Performance API временно недоступен.',True) from None
        if response.status_code==429:
            response.close();raise PerformanceError('rate_limit','Лимит Performance API: повторите проверку позже.',True)
        if response.status_code>=400:
            status=response.status_code;response.close();raise PerformanceError('http_'+str(status),'Performance API отклонил запрос.',status>=500)
        if 300<=response.status_code<400:
            response.close();raise PerformanceError('redirect','Неожиданное перенаправление Performance API.')
        return response
    def _json(self,response):
        try:
            data=self._bytes(response)
            result=json.loads(data)
            if not isinstance(result,dict):raise ValueError()
            return result
        except PerformanceError:raise
        except ValueError:raise PerformanceError('schema','Неподдерживаемый ответ Performance API.') from None
        finally:response.close()
    def _bytes(self,response):
        deadline=self.clock()+12;parts=[];size=0
        try:
            for chunk in response.iter_content(chunk_size=65536):
                size+=len(chunk)
                if size>MAX_REPORT_BYTES or self.clock()>deadline:raise PerformanceError('report_limit','Ответ превышает безопасный размер или время загрузки.')
                parts.append(chunk)
            return b''.join(parts)
        except requests.RequestException:raise PerformanceError('network','Не удалось прочитать ответ Performance API.',True) from None
    def _token(self):
        if self.token and self.clock()<self.expires:return self.token
        obj=self._json(self._raw('POST','/api/client/token',headers={'Content-Type':'application/json'},json={'client_id':self.client_id,'client_secret':self.client_secret,'grant_type':'client_credentials'}))
        token=obj.get('access_token');ttl=obj.get('expires_in');kind=obj.get('token_type')
        if not isinstance(token,str) or not token or len(token)>8192 or any(ord(c)<32 for c in token) or type(ttl) not in (int,float) or not math.isfinite(ttl) or ttl<=0 or str(kind).lower()!='bearer':raise PerformanceError('oauth','Не удалось авторизовать отдельный ключ Performance API.')
        self.token=token;self.expires=self.clock()+min(float(ttl),86400)-min(60,float(ttl)/2)
        return token
    def _authorized(self,method,path,**kwargs):
        for attempt in range(2):
            try:return self._raw(method,path,headers={'Authorization':'Bearer '+self._token(),'Content-Type':'application/json'},**kwargs)
            except PerformanceError as error:
                if error.code=='http_401' and attempt==0:self.token=None;self.expires=0;continue
                raise
    def submit(self,campaign_ids,start,end):
        obj=self._json(self._authorized('POST','/api/client/statistics',json={'campaigns':campaign_ids,'dateFrom':start,'dateTo':end,'groupBy':'DATE'}))
        try:return str(uuid.UUID(obj['UUID']))
        except (ValueError,KeyError,TypeError):raise PerformanceError('schema','Отчёт Performance API не получил идентификатор.') from None
    def status(self,remote_uuid):
        return self._json(self._authorized('GET','/api/client/statistics/'+str(uuid.UUID(remote_uuid))))
    def report(self,remote_uuid):
        response=self._authorized('GET','/api/client/statistics/report',params={'UUID':str(uuid.UUID(remote_uuid))},stream=True)
        try:
            return self._bytes(response)
        finally:response.close()

def number(value,count=False):
    if value is None or not str(value).strip():return None
    text=str(value).strip().replace('\u00a0','').replace(' ','').replace(',','.')
    try:
        result=Decimal(text)
        if not result.is_finite() or result<0 or result>Decimal('1000000000000') or (count and result!=result.to_integral_value()):raise ValueError()
        return int(result) if count else str(result)
    except (InvalidOperation,ValueError):raise PerformanceError('schema','Рекламный отчёт содержит некорректное число.') from None

def _decode(payload):
    try:return payload.decode('utf-8-sig')
    except UnicodeDecodeError:
        try:return payload.decode('cp1251')
        except UnicodeDecodeError:raise PerformanceError('schema','Неподдерживаемая кодировка рекламного отчёта.') from None

def parse_csv(payload,start,end,expected_campaigns):
    """Recognize direct attribution columns; never sum the final total twice."""
    text=_decode(payload);match=re.search(r'№\s*([1-9][0-9]*)',text)
    if not match:raise PerformanceError('schema','В CSV нет подтверждённого номера рекламной кампании.')
    campaign=match.group(1)
    if campaign not in set(expected_campaigns):raise PerformanceError('scope','Отчёт относится к другой рекламной кампании.')
    window=re.search(r'период\s+(\d{2}\.\d{2}\.\d{4})\s*-\s*(\d{2}\.\d{2}\.\d{4})',text,re.I)
    if not window:raise PerformanceError('scope','В CSV нет подтверждённого периода рекламного отчёта.')
    try:reported=[datetime.strptime(x,'%d.%m.%Y').date().isoformat() for x in window.groups()]
    except ValueError:raise PerformanceError('scope','Некорректный период рекламного отчёта.') from None
    if reported!=[start,end]:raise PerformanceError('scope','Период рекламного отчёта не совпадает с запросом.')
    lines=[line.strip() for line in text.splitlines() if line.strip()]
    # The official example quotes each entire semicolon-delimited line.
    lines=[line[1:-1].replace('""','"') if line.startswith('"') and line.endswith('"') and ';' in line else line for line in lines]
    header_index=next((i for i,line in enumerate(lines) if 'Показы' in line and 'Клики' in line and ';' in line),None)
    if header_index is None:raise PerformanceError('schema','Неподдерживаемые колонки рекламного CSV.')
    reader=csv.DictReader(lines[header_index:],delimiter=';');headers=reader.fieldnames or []
    if any(re.search(r'\b(?:USD|EUR|CNY|KZT|AED)\b|[$€¥]',header,re.I) for header in headers):raise PerformanceError('currency','Рекламный отчёт содержит неподдерживаемую валюту; пересчёт не выполняется.')
    currency_header=next((h for h in headers if h.strip().lower() in {'валюта','currency'}),None)
    mapping={}
    for metric,label in HEADERS.items():
        mapping[metric]=next((h for h in headers if h.strip()==label or h.strip().startswith(label+',')),None)
    day_header=next((h for h in headers if h.strip() in {'Дата','День'}),None)
    rows=[];total=[]
    for row in reader:
        if len(rows)+len(total)>=MAX_ROWS or None in row:raise PerformanceError('schema','Слишком большой или некорректный рекламный CSV.')
        first=str(row.get(headers[0]) or '').strip()
        if not first:continue
        if currency_header and str(row.get(currency_header) or '').strip().upper() not in {'RUB','643','РУБ','₽'}:raise PerformanceError('currency','Валюта строки рекламного отчёта не подтверждена как RUB.')
        day=None
        is_total=first.lower()=='всего'
        if not is_total and day_header and str(row.get(day_header) or '').strip():
            raw=row[day_header].strip()
            try:day=date.fromisoformat(raw).isoformat()
            except ValueError:
                try:day=datetime.strptime(raw,'%d.%m.%Y').date().isoformat()
                except ValueError:raise PerformanceError('schema','Некорректная дата рекламного отчёта.') from None
            if not start<=day<=end:raise PerformanceError('scope','Дата рекламного отчёта вне запрошенного периода.')
        entry={'campaign_id':campaign,'day':day,**{m:number(row.get(h),m in COUNTS) if h else None for m,h in mapping.items()}}
        if is_total:total.append(entry)
        else:rows.append(entry)
    if rows and total:
        if len(total)!=1:raise PerformanceError('schema','В отчёте неоднозначные итоговые строки.')
        for metric in METRICS:
            if total[0][metric] is not None and all(r[metric] is not None for r in rows):
                if sum(Decimal(str(r[metric])) for r in rows)!=Decimal(str(total[0][metric])):raise PerformanceError('reconciliation','Итог рекламного отчёта не совпадает с детализацией.')
    if not rows:rows=total
    if not rows:raise PerformanceError('empty_report','Нет явных строк или итогов рекламного отчёта; нули не подтверждены.')
    return rows

def parse_report(payload,start,end,campaign_ids):
    if len(payload)>MAX_REPORT_BYTES:raise PerformanceError('report_limit','Слишком большой рекламный отчёт.')
    rows=[]
    if payload.startswith(b'PK'):
        try:
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                files=[x for x in archive.infolist() if not x.is_dir() and not x.filename.startswith('__MACOSX/')]
                if not files or len(files)>10 or sum(x.file_size for x in files)>MAX_REPORT_BYTES:raise ValueError()
                for item in files:
                    if not item.filename.lower().endswith('.csv') or item.flag_bits&1 or item.file_size>MAX_REPORT_BYTES:raise ValueError()
                    part=parse_csv(archive.read(item),start,end,campaign_ids)
                    if {x['campaign_id'] for x in rows}&{x['campaign_id'] for x in part}:raise PerformanceError('schema','Архив содержит повторяющиеся отчёты кампании.')
                    rows+=part
        except (zipfile.BadZipFile,ValueError,RuntimeError):raise PerformanceError('schema','Неподдерживаемый архив рекламного отчёта.') from None
    else:rows=parse_csv(payload,start,end,campaign_ids)
    if len(rows)>MAX_ROWS or {x['campaign_id'] for x in rows}!=set(campaign_ids):raise PerformanceError('incomplete','Не все выбранные рекламные кампании вошли в отчёт.')
    return rows

def metrics(rows):
    result={};exact={}
    for field in METRICS:
        values=[r.get(field) for r in rows]
        exact[field]=None if not rows or any(v is None for v in values) else sum(Decimal(str(v)) for v in values)
        result[field]=None if exact[field] is None else (int(exact[field]) if field in COUNTS else format(exact[field].quantize(Decimal('0.01'),rounding=ROUND_HALF_UP),'f'))
    def ratio(a,b,factor=1,money=False):
        if exact[a] is None or exact[b] is None or exact[b]==0:return None
        value=(exact[a]/exact[b]*factor).quantize(Decimal('0.01'),rounding=ROUND_HALF_UP)
        return format(value,'f') if money else float(value)
    return {**result,'ctr':ratio('clicks','views',100),'cpc':ratio('spend','clicks',money=True),'cpm':ratio('spend','views',1000,money=True),'cpo':ratio('spend','orders',money=True),'drr':ratio('spend','ad_revenue',100),'order_conversion':ratio('orders','clicks',100)}

def schema(conn):
    conn.execute('CREATE TABLE IF NOT EXISTS ozon_ads_reports(id TEXT PRIMARY KEY,remote_uuid TEXT NOT NULL,start_date TEXT NOT NULL,end_date TEXT NOT NULL,campaign_ids TEXT NOT NULL,credential_fingerprint TEXT NOT NULL,status TEXT NOT NULL,polls INTEGER NOT NULL,created_at TEXT NOT NULL,error_code TEXT)')
    conn.execute('CREATE TABLE IF NOT EXISTS ozon_ads_snapshots(period_key TEXT PRIMARY KEY,revision TEXT NOT NULL,created_at TEXT NOT NULL,payload_json TEXT NOT NULL)')

def report_job(identifier):
    if not isinstance(identifier,str) or not re.fullmatch('[a-f0-9]{32}',identifier):raise ValueError('Рекламный отчёт не найден.')
    with connect() as conn:
        schema(conn);row=conn.execute('SELECT * FROM ozon_ads_reports WHERE id=?',(identifier,)).fetchone()
        if row is None:raise ValueError('Рекламный отчёт не найден.')
        return dict(row)
def public_job(row):return {k:row[k] for k in ('id','start_date','end_date','status','polls','created_at','error_code')}

def start_report(client,value,start,end,campaign_ids):
    start,end=period(start,end);campaign_ids=[str(x) for x in campaign_ids]
    if not 1<=len(campaign_ids)<=10 or len(set(campaign_ids))!=len(campaign_ids) or any(not re.fullmatch('[1-9][0-9]{0,19}',x) or int(x)>2**64-1 for x in campaign_ids):raise ValueError('Выберите от 1 до 10 разных кампаний Performance API.')
    remote=client.submit(campaign_ids,start,end);identifier=uuid.uuid4().hex
    with connect() as conn:
        schema(conn);conn.execute('INSERT INTO ozon_ads_reports VALUES (?,?,?,?,?,?,?,?,?,?)',(identifier,remote,start,end,json.dumps(campaign_ids),credential_fingerprint(value),'pending',0,datetime.now(timezone.utc).isoformat(),None))
    return public_job(report_job(identifier))

def poll_report(client,value,identifier):
    job=report_job(identifier)
    if job['status']!='pending':return public_job(job)
    if job['credential_fingerprint']!=credential_fingerprint(value):raise ValueError('Ключ Performance изменился; создайте новый отчёт.')
    if job['polls']>=MAX_POLLS or (datetime.now(timezone.utc)-datetime.fromisoformat(job['created_at'])).total_seconds()>86400:
        state='expired';error='report_expired'
    else:
        state='pending';error=None
        try:
            status=client.status(job['remote_uuid']);remote_state=str(status.get('state','')).upper()
            if status.get('kind') not in (None,'STATS'):raise PerformanceError('schema','Ожидался обычный статистический рекламный отчёт.')
            request=status.get('request')
            if request is not None:
                if not isinstance(request,dict) or set(map(str,request.get('campaigns',[])))!=set(json.loads(job['campaign_ids'])) or request.get('dateFrom')!=job['start_date'] or request.get('dateTo')!=job['end_date'] or request.get('groupBy')!='DATE':raise PerformanceError('scope','Параметры готового рекламного отчёта не совпадают с запросом.')
            if remote_state=='ERROR':state='failed';error='remote_report_failed'
            elif remote_state=='OK':
                payload=client.report(job['remote_uuid']);rows=parse_report(payload,job['start_date'],job['end_date'],json.loads(job['campaign_ids']))
                save_snapshot(job,rows);state='complete'
            elif remote_state not in {'NOT_STARTED','IN_PROGRESS'}:raise PerformanceError('schema','Неизвестный статус рекламного отчёта.')
        except PerformanceError as exc:
            error=exc.code
            if not exc.retryable:state='failed'
    with connect() as conn:
        schema(conn);conn.execute('UPDATE ozon_ads_reports SET status=?,polls=polls+1,error_code=? WHERE id=?',(state,error,identifier))
    return public_job(report_job(identifier))

def save_snapshot(job,rows):
    payload={'start':job['start_date'],'end':job['end_date'],'campaign_ids':json.loads(job['campaign_ids']),'rows':rows,'scope':'selected_campaigns','currency':'RUB','attribution':'direct_report_sales','source_kind':'ozon-performance-cpc-csv'}
    encoded=json.dumps(payload,sort_keys=True,separators=(',',':'));revision=hashlib.sha256(encoded.encode()).hexdigest()
    with connect() as conn:
        schema(conn);conn.execute('INSERT INTO ozon_ads_snapshots VALUES (?,?,?,?) ON CONFLICT(period_key) DO UPDATE SET revision=excluded.revision,created_at=excluded.created_at,payload_json=excluded.payload_json',(job['start_date']+':'+job['end_date'],revision,datetime.now(timezone.utc).isoformat(),encoded))

def analytics(start,end):
    start,end=period(start,end)
    with connect() as conn:
        schema(conn);saved=conn.execute('SELECT * FROM ozon_ads_snapshots WHERE period_key=?',(start+':'+end,)).fetchone()
    if saved is None:return {'start':start,'end':end,'loaded':False,'has_data':False,'totals':metrics([]),'campaigns':[],'by_day':[],'by_day_available':False,'source_complete':False,'marketplace':'ozon'}
    payload=json.loads(saved['payload_json']);rows=payload['rows'];campaigns=[{'campaign_id':cid,**metrics([r for r in rows if r['campaign_id']==cid])} for cid in payload['campaign_ids']]
    daily=all(r['day'] is not None for r in rows);by_day=[{'day':day,**metrics([r for r in rows if r['day']==day])} for day in sorted({r['day'] for r in rows})] if daily else []
    totals=metrics(rows)
    return {'start':start,'end':end,'loaded':True,'has_data':bool(rows),'totals':totals,'campaigns':campaigns,'by_day':by_day,'by_day_available':daily,'daily_scope':'reported_days','source_received_complete':True,'source_complete':all(totals[k] is not None for k in ('views','clicks','spend','orders','ad_revenue')),'revision':saved['revision'],'loaded_at':saved['created_at'],'scope':payload['scope'],'currency':'RUB','attribution':payload['attribution'],'marketplace':'ozon','coverage':{m:{'known_rows':sum(r.get(m) is not None for r in rows),'total_rows':len(rows)} for m in METRICS},'definitions':{'drr':'Расход с НДС / прямые продажи из рекламного отчёта × 100; продажи модели отдельно.','cpo':'Расход / заказы из рекламного отчёта.','scope':'Только явно выбранные кампании; не вся реклама магазина.'}}

def export_csv(start,end):
    result=analytics(start,end);output=io.StringIO();writer=csv.writer(output,delimiter=';');fields=['campaign_id','views','clicks','spend','orders','ad_revenue','ctr','cpc','cpm','cpo','drr'];writer.writerow(fields)
    for row in result['campaigns']:writer.writerow([row.get(k) if row.get(k) is not None else '' for k in fields])
    return '\ufeff'+output.getvalue()
