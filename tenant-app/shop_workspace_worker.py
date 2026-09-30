"""Child-only calculation/sync process. No cross-shop globals in the API process."""
from __future__ import annotations
import json, os, sys
from datetime import date
from pathlib import Path

def main():
    payload=json.load(sys.stdin)
    import config
    config.get_token=lambda: payload['token']
    config.get_ozon_credentials=lambda: None
    from db import init_db
    from db.core import ensure_financial_schema
    init_db()
    ensure_financial_schema()
    from db.core import connect
    with connect() as conn:
        conn.execute('CREATE TABLE IF NOT EXISTS shop_private_settings(id INTEGER PRIMARY KEY CHECK(id=1),payload_json TEXT NOT NULL)')
        previous=conn.execute('SELECT payload_json FROM shop_private_settings WHERE id=1').fetchone()
    if previous:config.save_settings(json.loads(previous['payload_json']))
    import shop_report_assets
    shop_report_assets.restore()
    def persist_settings():
        with connect() as conn:
            conn.execute('INSERT INTO shop_private_settings VALUES(1,?) ON CONFLICT(id) DO UPDATE SET payload_json=excluded.payload_json',
                         (json.dumps(config.load_settings(),allow_nan=False),))
    from shop_worker_policy import install
    install()
    action=payload['action']
    sync_errors=payload.get('source_errors',[])
    if action=='sync':
        from sync import sync_all
        config.save_settings({'initial_history_days':payload['days'],'advert_history_days':payload['days']})
        outcome=sync_all()
        sync_errors=[k for k,v in outcome.items() if v and (k.endswith('_error') or k.endswith('_skipped') or k=='fifo_errors')]
        sync_errors.append('shared_fifo_pending')
        settings=config.load_settings();settings['shop_source_errors']=sync_errors;config.save_settings(settings)
    elif action=='cost':
        import pandas as pd
        from db import save_costs
        from db.core import connect
        with connect() as c:
            if not c.execute('SELECT 1 FROM products_catalog WHERE nm_id=?',(payload['cost']['nm_id'],)).fetchone():
                raise ValueError('Product is not in this shop')
        save_costs(pd.DataFrame([payload['cost']]), source='shop_workspace', actor=payload.get('actor'))
    elif action=='request':
        import asyncio,base64,importlib.util,httpx
        import shop_context
        envelope=payload['request']
        scope=shop_context.route_scope(envelope['method'],envelope['path'],envelope.get('query',''))
        if not scope or scope['scope']!='shop_local':raise ValueError('Unsupported child route')
        api_directory=Path(__file__).resolve().parents[1]/'mh-redesign-lab'/'tenant-api'
        if not api_directory.is_dir():api_directory=Path(__file__).resolve().parent
        sys.path.insert(0,str(api_directory))
        spec=importlib.util.spec_from_file_location('shop_private_api',api_directory/'api_server.py')
        api=importlib.util.module_from_spec(spec);sys.modules[spec.name]=api;spec.loader.exec_module(api)
        api._startup()
        captured_outcomes=[]
        def capture(function):
            def run(*args,**kwargs):
                outcome=function(*args,**kwargs)
                captured_outcomes.append(outcome)
                return outcome
            return run
        for name in ('sync_all','sync_ads_only','sync_finances'):
            setattr(api,name,capture(getattr(api,name)))
        # Finish private import jobs before snapshotting; no daemon mutation
        # may escape after a response or be lost when this child exits.
        api.sync_runner.start=lambda job:(job(),True)[1]
        body=base64.b64decode(envelope['body'],validate=True)
        if len(body)>1024*1024:raise ValueError('Child body too large')
        async def dispatch():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app),base_url='http://127.0.0.1') as client:
                return await client.request(envelope['method'],envelope['path']+('?' + envelope['query'] if envelope['query'] else ''),
                  content=body,headers={'content-type':envelope.get('content_type','application/json'),
                    'x-tenant-account':payload['actor'],'x-tenant-role':payload['role']})
        response=asyncio.run(dispatch())
        if envelope['method']=='POST' and response.status_code<400 and (
            envelope['path']=='/api/v1/operations/reports/quick' or envelope['path'].endswith('/generate')):
            shop_report_assets.generate_one(response.json()['run_id'])
        if envelope['method']=='POST' and envelope['path'].startswith('/api/v1/sync') and response.status_code<400:
            if envelope['path']=='/api/v1/sync':sync_errors=[]
            for outcome in captured_outcomes:
                if isinstance(outcome,dict):
                    sync_errors.extend(str(k) for k,v in outcome.items() if v and
                                       (k.endswith('_error') or k.endswith('_skipped') or k=='fifo_errors'))
            from db.core import last_sync
            status=last_sync() or {}
            if status.get('status') in {'partial','error'}:sync_errors.append('sync_'+status['status'])
            sync_errors=sorted(set(sync_errors+['shared_fifo_pending']))
            config.save_settings({**config.load_settings(),'shop_source_errors':sync_errors})
        if len(response.content)>4*1024*1024:raise ValueError('Child response too large')
        content=response.content
        if response.headers.get('content-type','').startswith('application/json') and '/reports' in envelope['path']:
            def public_paths(value):
                if isinstance(value,dict):return {key:(Path(item).name if key=='file_path' and isinstance(item,str) else public_paths(item)) for key,item in value.items()}
                if isinstance(value,list):return [public_paths(item) for item in value]
                return value
            value=public_paths(response.json())
            if envelope['path']=='/api/v1/dashboard/reports':
                for exported in value.get('exports',[]):
                    if isinstance(exported.get('name'),str):exported['name']=Path(exported['name']).name
            content=json.dumps(value,allow_nan=False).encode()
        result={'status':response.status_code,'body':base64.b64encode(content).decode(),
                'content_type':response.headers.get('content-type','application/json'),
                'content_disposition':response.headers.get('content-disposition'),
                'sync_errors':sync_errors}
        persist_settings()
        Path(os.environ['SHOP_RESULT_PATH']).write_text(json.dumps(result,allow_nan=False),encoding='utf-8')
        return
    elif action!='report':
        raise ValueError('Unsupported shop action')
    from calculations import build_dashboard
    from db import read_table
    from today_service import json_safe
    start,end=date.fromisoformat(payload['start']),date.fromisoformat(payload['end'])
    dashboard=build_dashboard(start,end)
    costs=read_table('costs')
    cards=read_table('products_catalog')
    products=[]
    if not cards.empty:
        for _, row in cards.head(500).iterrows():
            nm_id=int(row.get('nm_id') or 0)
            item={'nm_id':nm_id,'name':str(row.get('product_name') or row.get('supplier_article') or nm_id)}
            match=costs[costs['nm_id']==nm_id] if not costs.empty else costs
            item['cost']=float(match.iloc[0].get('cost_per_wb_unit') or 0) if not match.empty else None
            products.append(item)
    report=json_safe({'financial':dashboard.financial,'products':products,'stocks':dashboard.stocks.head(500).to_dict('records'), 'product_count':len(cards), 'stock_row_count':len(dashboard.stocks), 'sync_errors':sync_errors,'sources_complete':not sync_errors})
    persist_settings()
    # Dedicated output file: logs and credential-bearing exceptions cannot enter API output.
    Path(os.environ['SHOP_RESULT_PATH']).write_text(json.dumps(report,ensure_ascii=False,allow_nan=False),encoding='utf-8')

if __name__=='__main__':
    main()
