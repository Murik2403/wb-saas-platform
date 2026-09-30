"""Explicit canonical CSV statements, never guessed bank layouts."""
import csv
import io
import json
import re
from decimal import Decimal
from datetime import date
from .core import connect
from . import cash_accounts as accounts
from . import cashflow

COLUMNS=['external_id','date','direction','amount','currency','exchange_rate','category','reference','plan_id']
CATEGORIES={'marketplace_payout','procurement','salary','tax','rent','advertising','other'}

def parse(text):
    if not isinstance(text,str) or len(text.encode('utf-8'))>1048576: raise ValueError('Выписка превышает 1 МБ')
    reader=csv.DictReader(io.StringIO(text.lstrip('\ufeff')),delimiter=';')
    if reader.fieldnames!=COLUMNS: raise ValueError('Нужны столбцы: '+';'.join(COLUMNS))
    rows=[]; seen=set()
    for index,row in enumerate(reader,2):
        if index>1001: raise ValueError('Допустимо не более 1000 платежей')
        if None in row or any(v is None for v in row.values()): raise ValueError(f'Строка {index}: неверное число столбцов')
        row={k:v.strip() for k,v in row.items()}
        if len(row['amount'])>40 or len(row['exchange_rate'])>40 or len(row['date'])!=10 or len(row['plan_id'])>15: raise ValueError('Недопустимая длина суммы, курса, даты или номера плана')
        if not re.fullmatch(r'\d+(\.\d{1,2})?',row['amount']) or not re.fullmatch(r'\d+(\.\d{1,8})?',row['exchange_rate']): raise ValueError('Сумма и курс требуют десятичного формата с точкой')
        if not row['external_id'] or len(row['external_id'])>100 or row['external_id'] in seen: raise ValueError(f'Строка {index}: нужен уникальный идентификатор банка')
        seen.add(row['external_id'])
        if len(row['reference'])>300 or row['category'] not in CATEGORIES: raise ValueError(f'Строка {index}: неверная статья или описание')
        row['date']=date.fromisoformat(row['date']).isoformat()
        if row['plan_id'] and (not row['plan_id'].isascii() or not row['plan_id'].isdigit() or int(row['plan_id'])<1): raise ValueError('Неверный номер плана')
        row['plan_id']=int(row['plan_id']) if row['plan_id'] else None
        body=body_for(row,'preview')
        cashflow.validate_entry(body); accounts.native_amount(row['amount'])
        rows.append(row)
    if not rows: raise ValueError('Выписка пуста')
    return rows

def body_for(row,key):
    return dict(request_key=key,kind='movement',direction=row['direction'],status='actual',planned_date=row['date'],actual_date=row['date'],amount=row['amount'],exchange_rate=row['exchange_rate'],currency=row['currency'],category=row['category'],reference=row['reference'])

def preview_in(conn,account_id,rows):
    wallet=accounts.account(conn,account_id)
    if wallet['legacy']: raise ValueError('Выберите отдельный банковский счёт')
    result=[]; plans=set()
    for row in rows:
        if row['currency']!=wallet['currency']: raise ValueError('Валюта выписки не совпадает со счётом')
        digest=accounts.fingerprint(row)
        existing=conn.execute('SELECT * FROM cash_account_entries WHERE account_id=? AND external_key=?',(account_id,row['external_id'])).fetchone()
        item=dict(row,action='create',fingerprint=digest)
        if existing:
            if existing['external_fingerprint']!=digest: raise ValueError('Идентификатор банка уже сохранён с другими данными')
            item.update(action='skip',entry_id=existing['entry_id'])
        elif row['plan_id']:
            if row['plan_id'] in plans: raise ValueError('Один план нельзя подтвердить двумя платежами')
            plans.add(row['plan_id'])
            plan=conn.execute('SELECT e.*,m.account_id,m.native_minor,m.source_kind,m.external_key FROM cash_entries e JOIN cash_account_entries m ON m.entry_id=e.id WHERE e.id=?',(row['plan_id'],)).fetchone()
            if not plan or plan['account_id']!=account_id or plan['status']!='planned' or plan['kind']!='movement' or plan['source_kind']=='transfer' or plan['external_key']:
                raise ValueError('Выбранный план недоступен для подтверждения')
            if plan['direction']!=row['direction'] or plan['native_minor']!=accounts.native_amount(row['amount']) or Decimal(plan['exchange_rate'])!=Decimal(row['exchange_rate']):
                raise ValueError('Сумма, направление или курс плана не совпадают с выпиской')
            if plan['category']!=row['category']: raise ValueError('Статья плана не совпадает с выпиской')
            item.update(action='settle',entry_id=plan['id'],plan_reference=plan['reference'],plan_state=accounts.fingerprint(dict(plan)))
        result.append(item)
    snapshot=dict(account_id=account_id,revision=wallet['revision'],rows=result)
    return dict(snapshot,fingerprint=accounts.fingerprint(snapshot),created=sum(r['action']=='create' for r in result),settled=sum(r['action']=='settle' for r in result),skipped=sum(r['action']=='skip' for r in result))

def preview(account_id,text):
    rows=parse(text)
    with connect() as conn:
        accounts.initialize(conn)
        return preview_in(conn,account_id,rows)

def confirm(account_id,text,expected,request_key,confirmed,actor=None):
    if confirmed is not True: raise ValueError('Подтвердите импорт после просмотра')
    rows=parse(text)
    replay=accounts.fingerprint(dict(account_id=account_id,rows=rows,expected=expected))
    with connect() as conn:
        accounts.initialize(conn); conn.execute('BEGIN IMMEDIATE')
        old=conn.execute('SELECT * FROM cash_statement_imports WHERE request_key=?',(request_key,)).fetchone()
        if old:
            if old['fingerprint']!=replay: raise ValueError('Ключ импорта уже использован')
            return json.loads(old['result_json'])
        result=preview_in(conn,account_id,rows)
        if result['fingerprint']!=expected: raise ValueError('Данные изменились; повторите предварительную проверку')
        for index,row in enumerate(result['rows']):
            if row['action']=='skip': continue
            raw=rows[index]
            if row['action']=='settle':
                entry=conn.execute('SELECT * FROM cash_entries WHERE id=?',(row['entry_id'],)).fetchone()
                accounts.set_status(conn,entry,'actual',row['date'],actor)
                conn.execute("UPDATE cash_account_entries SET source_kind='statement',external_key=?,external_fingerprint=?,bank_reference=? WHERE entry_id=?",(row['external_id'],row['fingerprint'],row['reference'],entry['id']))
            else:
                entry=accounts.insert(conn,account_id,body_for(raw,'statement_'+accounts.fingerprint([account_id,row['external_id']])[:40]),actor,'statement',row['external_id'],row['fingerprint'])
                row['entry_id']=entry['id']
        conn.execute('INSERT INTO cash_statement_imports(request_key,fingerprint,account_id,result_json,created_at,actor) VALUES(?,?,?,?,?,?)',(request_key,replay,account_id,accounts.canonical(result),cashflow._now(),actor))
        return result
