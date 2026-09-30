"""Tenant-local cash wallets; native balances never imply currency conversion."""
import hashlib
import json
import uuid
from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from .core import connect
from . import cashflow

CURRENCIES = {'RUB', 'CNY', 'USD', 'EUR'}

def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':'))

def fingerprint(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()

def initialize(conn):
    cashflow.initialize(conn)
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS cash_accounts(id INTEGER PRIMARY KEY, name TEXT NOT NULL,
      currency TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, revision INTEGER NOT NULL DEFAULT 1,
      legacy INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, actor TEXT);
    CREATE TABLE IF NOT EXISTS cash_account_entries(entry_id INTEGER PRIMARY KEY REFERENCES cash_entries(id),
      account_id INTEGER NOT NULL REFERENCES cash_accounts(id), native_minor INTEGER NOT NULL,
      source_kind TEXT NOT NULL, external_key TEXT, external_fingerprint TEXT,
      UNIQUE(account_id,external_key));
    CREATE TABLE IF NOT EXISTS cash_transfers(id INTEGER PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
      fingerprint TEXT NOT NULL, from_entry_id INTEGER NOT NULL UNIQUE, to_entry_id INTEGER NOT NULL UNIQUE,
      created_at TEXT NOT NULL, actor TEXT);
    CREATE TABLE IF NOT EXISTS cash_statement_imports(id INTEGER PRIMARY KEY, request_key TEXT NOT NULL UNIQUE,
      fingerprint TEXT NOT NULL, account_id INTEGER NOT NULL, result_json TEXT NOT NULL,
      created_at TEXT NOT NULL, actor TEXT);
    """)
    for table in ('cash_transfers','cash_statement_imports'):
        for op in ('UPDATE','DELETE'):
            conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_{op.lower()} BEFORE {op} ON {table} BEGIN SELECT RAISE(ABORT,'cash receipt is immutable'); END")
        conn.execute(f"CREATE TRIGGER IF NOT EXISTS {table}_no_replace BEFORE INSERT ON {table} WHEN EXISTS(SELECT 1 FROM {table} WHERE id=NEW.id OR request_key=NEW.request_key) BEGIN SELECT RAISE(ABORT,'cash receipt is immutable'); END")
    if 'request_key' not in {r[1] for r in conn.execute('PRAGMA table_info(cash_accounts)')}:
        conn.execute('ALTER TABLE cash_accounts ADD COLUMN request_key TEXT')
    conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS cash_accounts_request ON cash_accounts(request_key)')
    if 'bank_reference' not in {r[1] for r in conn.execute('PRAGMA table_info(cash_account_entries)')}:
        conn.execute('ALTER TABLE cash_account_entries ADD COLUMN bank_reference TEXT')
    conn.execute("INSERT OR IGNORE INTO cash_accounts(id,name,currency,legacy,created_at) VALUES(1,?,'RUB',1,?)", ('Исторический журнал — оценка в RUB',cashflow._now()))
    # Historical foreign currency amounts are valuations, not proven bank balances.
    conn.execute("INSERT OR IGNORE INTO cash_account_entries(entry_id,account_id,native_minor,source_kind) SELECT id,1,amount_minor,'legacy' FROM cash_entries")
    conn.commit()

def account(conn, account_id, active=True):
    row=conn.execute('SELECT * FROM cash_accounts WHERE id=?',(account_id,)).fetchone()
    if not row: raise LookupError('Счёт не найден')
    if active and not row['active']: raise ValueError('Счёт архивирован')
    return dict(row)

def create_account(name,currency,actor=None,request_key=None):
    name=str(name).strip()
    if not name or len(name)>100 or currency not in CURRENCIES: raise ValueError('Укажите название и поддерживаемую валюту')
    with connect() as conn:
        initialize(conn); conn.execute('BEGIN IMMEDIATE')
        request_key=request_key or str(uuid.uuid4())
        old=conn.execute('SELECT * FROM cash_accounts WHERE request_key=?',(request_key,)).fetchone()
        if old:
            if old['name']!=name or old['currency']!=currency: raise ValueError('Ключ создания счёта уже использован')
            return dict(old)
        if conn.execute('SELECT COUNT(*) FROM cash_accounts').fetchone()[0]>=50: raise ValueError('Допустимо не более 50 счетов')
        cursor=conn.execute('INSERT INTO cash_accounts(name,currency,created_at,actor,request_key) VALUES(?,?,?,?,?)',(name,currency,cashflow._now(),actor,request_key))
        return account(conn,cursor.lastrowid)

def archive_account(account_id,revision,active):
    with connect() as conn:
        initialize(conn); conn.execute('BEGIN IMMEDIATE'); row=account(conn,account_id,False)
        if row['legacy']: raise ValueError('Исторический журнал нельзя архивировать')
        if row['revision']!=revision: raise ValueError('Счёт изменился; обновите данные')
        conn.execute('UPDATE cash_accounts SET active=?,revision=revision+1 WHERE id=?',(int(active),account_id))
        return account(conn,account_id,False)

def native_amount(amount):
    value=Decimal(str(amount))
    if not value.is_finite() or value<0 or value>Decimal('10000000000000') or value!=value.quantize(Decimal('.01')):
        raise ValueError('Сумма счёта требует до двух знаков после запятой и допустимого предела')
    return int(value*100)

def insert(conn,account_id,payload,actor=None,source='manual',external_key=None,external_fingerprint=None):
    wallet=account(conn,account_id)
    if wallet['legacy']: raise ValueError('Для нового счёта выберите отдельный кошелёк')
    if payload['currency']!=wallet['currency']: raise ValueError('Валюта операции должна совпадать со счётом')
    data,amount,rate,minor,encoded=cashflow.validate_entry(payload)
    native=native_amount(amount)
    old=conn.execute('SELECT e.*,m.account_id,m.source_kind,m.external_key,m.external_fingerprint FROM cash_entries e LEFT JOIN cash_account_entries m ON m.entry_id=e.id WHERE e.request_key=?',(data['request_key'],)).fetchone()
    if old:
        source_matches=old['source_kind']==source or (source=='manual' and old['source_kind']=='statement')
        external_matches=source=='manual' or (old['external_key']==external_key and old['external_fingerprint']==external_fingerprint)
        if old['account_id']!=account_id or old['payload_json']!=encoded or not source_matches or not external_matches: raise ValueError('Ключ операции уже использован')
        return dict(old)
    if data['kind']=='opening' and conn.execute("SELECT 1 FROM cash_entries e JOIN cash_account_entries m ON m.entry_id=e.id WHERE m.account_id=? AND e.kind='opening' AND e.status!='cancelled'",(account_id,)).fetchone():
        raise ValueError('Сначала отмените предыдущий остаток этого счёта')
    row=cashflow._insert(conn,data,amount,rate,minor,encoded,actor,account_id)
    conn.execute('INSERT INTO cash_account_entries(entry_id,account_id,native_minor,source_kind,external_key,external_fingerprint,bank_reference) VALUES(?,?,?,?,?,?,?)',(row['id'],account_id,native,source,external_key,external_fingerprint,data['reference'] if source=='statement' else None))
    return row

def create_entry(account_id,payload,actor=None):
    with connect() as conn:
        initialize(conn); conn.execute('BEGIN IMMEDIATE')
        return insert(conn,account_id,payload,actor)

def transfer(payload,actor=None):
    data=dict(payload); digest=fingerprint(data)
    with connect() as conn:
        initialize(conn); conn.execute('BEGIN IMMEDIATE')
        old=conn.execute('SELECT * FROM cash_transfers WHERE request_key=?',(data['request_key'],)).fetchone()
        if old:
            if old['fingerprint']!=digest: raise ValueError('Ключ перевода уже использован')
            return dict(old)
        if data['from_account_id']==data['to_account_id']: raise ValueError('Выберите разные счета')
        src,dst=account(conn,data['from_account_id']),account(conn,data['to_account_id'])
        if src['currency']==dst['currency'] and native_amount(data['from_amount'])!=native_amount(data['to_amount']): raise ValueError('Перевод одной валюты требует одинаковых сумм')
        entries=[]
        for side,wallet,direction in [('from',src,'outflow'),('to',dst,'inflow')]:
            body=dict(request_key=data['request_key']+'_'+side,kind='movement',direction=direction,status=data['status'],planned_date=data['planned_date'],actual_date=data.get('actual_date'),amount=data[side+'_amount'],exchange_rate=data[side+'_rate'],currency=wallet['currency'],category='other',reference=data.get('reference',''))
            entries.append(insert(conn,wallet['id'],body,actor,'transfer'))
        cursor=conn.execute('INSERT INTO cash_transfers(request_key,fingerprint,from_entry_id,to_entry_id,created_at,actor) VALUES(?,?,?,?,?,?)',(data['request_key'],digest,entries[0]['id'],entries[1]['id'],cashflow._now(),actor))
        return dict(conn.execute('SELECT * FROM cash_transfers WHERE id=?',(cursor.lastrowid,)).fetchone())

def set_status(conn,row,status,actual_date,actor):
    if status not in {'actual','cancelled'}: raise ValueError('Недопустимый статус')
    if status=='actual': actual_date=date.fromisoformat(str(actual_date)).isoformat()
    elif actual_date: raise ValueError('Для отмены дата не нужна')
    if row['status']==status:
        if status=='actual' and row['actual_date']!=actual_date: raise ValueError('Платёж подтверждён другой датой')
        return
    if row['status']=='cancelled': raise ValueError('Отменённую операцию нельзя провести')
    conn.execute('UPDATE cash_entries SET status=?,actual_date=? WHERE id=?',(status,actual_date if status=='actual' else row['actual_date'],row['id']))
    conn.execute('INSERT INTO cash_entry_events(entry_id,status,actual_date,changed_at,actor) VALUES(?,?,?,?,?)',(row['id'],status,actual_date,cashflow._now(),actor))

def grouped_status(entry_id,status,actual_date,actor):
    with connect() as conn:
        cashflow.initialize(conn)
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='cash_transfers'").fetchone(): return None
        conn.execute('BEGIN IMMEDIATE')
        pair=conn.execute('SELECT * FROM cash_transfers WHERE from_entry_id=? OR to_entry_id=?',(entry_id,entry_id)).fetchone()
        if not pair: return None
        for key in ('from_entry_id','to_entry_id'):
            row=conn.execute('SELECT * FROM cash_entries WHERE id=?',(pair[key],)).fetchone()
            set_status(conn,row,status,actual_date,actor)
        return dict(conn.execute('SELECT * FROM cash_entries WHERE id=?',(entry_id,)).fetchone())

def report(start,end):
    start,end=date.fromisoformat(str(start)).isoformat(),date.fromisoformat(str(end)).isoformat()
    if start>end: raise ValueError('Начало периода позже окончания')
    with connect() as conn:
        initialize(conn)
        wallets=[dict(r) for r in conn.execute('SELECT * FROM cash_accounts ORDER BY id')]
        rows=[dict(r) for r in conn.execute('SELECT e.*,m.account_id,m.native_minor,m.source_kind,m.external_key AS bank_external_id,m.bank_reference FROM cash_entries e JOIN cash_account_entries m ON m.entry_id=e.id ORDER BY e.planned_date,e.id')]
    external_in=external_out=fx_delta=0
    for wallet in wallets:
        items=[r for r in rows if r['account_id']==wallet['id']]
        active=[r for r in items if r['status']!='cancelled']
        anchor=next((r for r in active if r['kind']=='opening'),None)
        anchor_date=anchor['planned_date'] if anchor else None
        valid=not anchor_date or start>=anchor_date
        def signed(r): return r['native_minor']*(1 if r['direction']=='inflow' else -1)
        actual=[r for r in active if r['kind']=='movement' and r['status']=='actual' and (not anchor_date or r['actual_date']>=anchor_date)]
        before=sum(signed(r) for r in actual if r['actual_date']<start)
        opening=(signed(anchor) if anchor and valid else 0)+before
        period=[r for r in actual if start<=r['actual_date']<=end]
        plans=[r for r in active if r['status']=='planned' and r['planned_date']<=end]
        running_actual,running_forecast=opening,opening
        daily=[]
        for day in sorted({r['actual_date'] for r in period}|{max(start,r['planned_date']) for r in plans}):
            delta=sum(signed(r) for r in period if r['actual_date']==day)
            planned=sum(signed(r) for r in plans if max(start,r['planned_date'])==day)
            running_actual+=delta; running_forecast+=delta+planned
            daily.append(dict(date=day,actual_minor=running_actual,forecast_minor=running_forecast))
        for r in period:
            if r['source_kind']=='transfer': fx_delta+=r['amount_minor']*(1 if r['direction']=='inflow' else -1)
            elif r['direction']=='inflow': external_in+=r['amount_minor']
            else: external_out+=r['amount_minor']
        wallet.update(daily=daily,opening_minor=opening,actual_closing_minor=opening+sum(signed(r) for r in period),forecast_closing_minor=opening+sum(signed(r) for r in period+plans),opening_confirmed=bool(anchor and valid),period_before_opening=not valid,overdue_minor=sum(signed(r) for r in plans if r['planned_date']<start),items=[{k:v for k,v in r.items() if k!='payload_json'} for r in items])
    return dict(accounts=wallets,start=start,end=end,external_inflow_rub_minor=external_in,external_outflow_rub_minor=external_out,transfer_valuation_delta_rub_minor=fx_delta,coverage='entered_cash_only',accrual_profit_unchanged=True)
