"""Verified per-request shop context; no database-path or credential globals."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
from urllib.parse import parse_qs
from db import shop_workspaces as shops,shared_warehouse as warehouse
from db.core import connect

ROUTES=json.loads(Path(__file__).with_name('shop_route_scopes.json').read_text(encoding='utf-8'))

class ShopContextError(ValueError):
    def __init__(self,code,message,status=403):self.code,self.message,self.status=code,message,status;super().__init__(message)

def selector(headers):
    value=headers.get('x-mh-shop-id','0')
    if not isinstance(value,str) or not re.fullmatch(r'0|[1-9][0-9]{0,8}',value):
        raise ShopContextError('SHOP_SELECTOR_INVALID','Магазин выбран некорректно',400)
    return int(value)

def route_scope(method,path,query=''):
    for route in ROUTES:
        expression=re.sub(r'\{[^}]+\}',r'[^/]+',route['path'])
        if route['method']==method and re.fullmatch(expression,path):
            if method=='GET' and path=='/api/v1/settings/change-history':
                kinds=parse_qs(query,keep_blank_values=True).get('kind',[])
                selected={'cost':'shop_local','production_norm':'tenant_shared'}.get(kinds[0]) if len(kinds)==1 else None
                return dict(route,scope=selected or 'forbidden')
            return route
    return None

def actor(headers,secondary=False):
    role=headers.get('x-tenant-role','viewer')
    account=headers.get('x-tenant-account')
    if secondary and (not account or 'x-tenant-role' not in headers):
        raise ShopContextError('SHOP_IDENTITY_REQUIRED','Для магазина требуется подтверждённый пользователь')
    return account,role

def initialize_bindings():
    # Legacy rows were inserted only after the owner API tested statistics.
    # Their frozen seller hash is reused, never unchecked token decoding.
    shops.initialize()
    with connect() as conn:warehouse.initialize(conn)
    with connect() as conn:
        verified=[dict(row) for row in conn.execute('SELECT id,account_identity FROM shop_workspaces WHERE active=1')]
    for record in verified:warehouse.register_shop(record['id'],record['account_identity'])

def authorize(shop_id,account,role,write=False):
    initialize_bindings()
    if shop_id==0:return None
    if not account:raise ShopContextError('SHOP_IDENTITY_REQUIRED','Нужен подтверждённый пользователь')
    try:
        with connect() as conn:
            conn.execute('BEGIN')
            record=conn.execute('SELECT id,name,account_identity,revision,active,database_snapshot IS NOT NULL AS snapshot_available FROM shop_workspaces WHERE id=? AND active=1',(shop_id,)).fetchone()
            if not record:raise ShopContextError('SHOP_NOT_AVAILABLE','Магазин не найден или отключён',409)
            scope=warehouse.access(conn,shop_id,account,role,write)
            if scope['identity']!=record['account_identity']:raise ShopContextError('SHOP_IDENTITY_CHANGED','Продавец магазина изменился',409)
            return dict(record)
    except warehouse.WarehouseError as exc:raise ShopContextError('SHOP_ACCESS_DENIED','Нет доступа к выбранному магазину') from exc

def session(headers,write_allowed):
    selected=selector(headers);account,role=actor(headers,selected!=0)
    initialize_bindings()
    primary_identity=None
    with connect() as conn:
        primary=conn.execute('SELECT identity FROM warehouse_shops WHERE shop_id=0 AND active=1').fetchone()
        if primary:primary_identity=primary['identity']
    can_write=role in {'owner','manager'}
    catalog=[{'id':0,'name':'Основной магазин','verified_identity':primary_identity,'active':True,
              'snapshot_revision':None,'snapshot_available':True,'can_write':can_write,
              'allowed_actions':[r['method']+' '+r['path'] for r in ROUTES if write_allowed(role,r['method'],r['path'])]}]
    if account and 'x-tenant-role' in headers:
        for item in shops.listing():
            if not item['active']:continue
            try:record=authorize(item['id'],account,role)
            except ShopContextError:continue
            try:authorize(item['id'],account,role,True);writable=True
            except ShopContextError:writable=False
            actions=[r['method']+' '+r['path'] for r in ROUTES if r['scope']!='forbidden' and
                     (r['method'] in {'GET','HEAD','OPTIONS'} or writable) and write_allowed(role,r['method'],r['path'])]
            catalog.append({'id':item['id'],'name':item['name'],'verified_identity':record['account_identity'],
              'active':True,'snapshot_revision':record['revision'],'snapshot_available':bool(record['snapshot_available']),
              'can_write':writable,'allowed_actions':actions})
    if selected not in {r['id'] for r in catalog}:raise ShopContextError('SHOP_ACCESS_DENIED','Выбранный магазин недоступен')
    return {'selected_shop_id':selected,'role':role,'shops':catalog,
            'route_scopes':[dict(r,action=r['method']+' '+r['path'],can_write=write_allowed(role,r['method'],r['path'])) for r in ROUTES]}

def response_headers(shop_id,record,scope):
    result={'X-MH-Shop-ID':str(shop_id),'X-MH-Resource-Scope':scope}
    if shop_id==0:
        with connect() as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='warehouse_shops'").fetchone():
                primary=conn.execute('SELECT identity FROM warehouse_shops WHERE shop_id=0 AND active=1').fetchone()
                if primary:result['X-MH-Shop-Identity']=primary['identity']
    if record is not None:
        result.update({'X-MH-Shop-Identity':record['account_identity'],'X-MH-Shop-Revision':str(record['revision']),
                       'X-MH-Shop-Fingerprint':warehouse.digest({'identity':record['account_identity'],'revision':record['revision']})})
    return result
