"""Child-only safety boundary: selected WB reads and no independent physical FIFO."""
from urllib.parse import urlsplit
import re
import sys

def install():
    from wb_api import WBAPI
    original=WBAPI._request
    allowed_posts=(r'/api/analytics/v1/stocks-report/wb-warehouses',
      r'/api/analytics/v3/sales-funnel/products',r'/api/v3/stocks/[0-9]+',r'/content/v2/get/cards/list')
    def read_only(self,method,url,**kwargs):
        parsed=urlsplit(url)
        known={urlsplit(getattr(WBAPI,name)).hostname for name in dir(WBAPI) if name.endswith('_URL')}
        if parsed.scheme!='https' or parsed.hostname not in known or not (
          method.upper()=='GET' or (method.upper()=='POST' and any(re.fullmatch(p,parsed.path) for p in allowed_posts))):
            raise ValueError('Операции изменения WB из дополнительного магазина запрещены')
        return original(self,method,url,**kwargs)
    WBAPI._request=read_only
    import db.fifo_finished_goods as finished
    originals=(finished._create_finished_goods_layer,finished._move_finished_goods_fifo)
    def no_physical(*args,**kwargs):
        from db.shared_warehouse import WarehouseScopeError
        raise WarehouseScopeError('Физический FIFO ведётся только центральным распределителем')
    # Replace already-imported aliases, not only the defining module.
    for name,module in list(sys.modules.items()):
        if name=='db' or name.startswith('db.'):
            for key,value in list(vars(module).items()):
                if any(value is original for original in originals):setattr(module,key,no_physical)
    import sync
    sync.sales_fifo_tracking_status=lambda:{'initialized':True}
    sync.initialize_sales_fifo_tracking=lambda:{'baseline':0}
    sync.process_sales_fifo_events=lambda:{'sales':0,'returns':0,'errors':0}
    import config
    def no_credentials(*args,**kwargs):raise ValueError('Подключение меняет только владелец в основном процессе')
    config.save_token=no_credentials
    config.delete_token=no_credentials
    # Historical independent child FIFO is not canonical physical provenance.
    # Keep its immutable records, but project costs as estimates until an
    # explicit matching canonical source receipt is supplied by the bridge.
    import db.financial_fifo_reconciliation as reconciliation
    original_reader=reconciliation.read_financial_fifo_reconciliation
    reconciliation.read_financial_fifo_reconciliation=lambda start,end:original_reader(start,end,allow_local_fifo=False)
