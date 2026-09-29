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
    action=payload['action']
    sync_errors=payload.get('source_errors',[])
    if action=='sync':
        from sync import sync_all
        config.save_settings({'initial_history_days':payload['days'],'advert_history_days':payload['days']})
        outcome=sync_all()
        sync_errors=[k for k,v in outcome.items() if v and (k.endswith('_error') or k.endswith('_skipped') or k=='fifo_errors')]
        settings=config.load_settings();settings['shop_source_errors']=sync_errors;config.save_settings(settings)
    elif action=='cost':
        import pandas as pd
        from db import save_costs
        from db.core import connect
        with connect() as c:
            if not c.execute('SELECT 1 FROM products_catalog WHERE nm_id=?',(payload['cost']['nm_id'],)).fetchone():
                raise ValueError('Product is not in this shop')
        save_costs(pd.DataFrame([payload['cost']]), source='shop_workspace', actor=payload.get('actor'))
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
    # Dedicated output file: logs and credential-bearing exceptions cannot enter API output.
    Path(os.environ['SHOP_RESULT_PATH']).write_text(json.dumps(report,ensure_ascii=False,allow_nan=False),encoding='utf-8')

if __name__=='__main__':
    main()
