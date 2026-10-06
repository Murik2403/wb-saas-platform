import pytest
from agents.marketplaces.ozon_client import OzonAgentClient

@pytest.mark.parametrize('operation',['get_campaigns','_performance_token','_performance_headers'])
def test_seller_credentials_never_used_for_performance_or_zero_campaigns(operation,monkeypatch):
    client=OzonAgentClient('seller-id','seller-key')
    monkeypatch.setattr(client.session,'request',lambda *args,**kwargs:pytest.fail('Seller credentials must not reach Performance API'))
    with pytest.raises(NotImplementedError,match='отдельный Performance API'):getattr(client,operation)()
@pytest.mark.parametrize('dry_run',[True,False])
@pytest.mark.parametrize('operation',['set_campaign_budget','pause_campaign','resume_campaign'])
def test_advertising_mutations_fail_closed_even_in_dry_run(operation,dry_run,monkeypatch):
    client=OzonAgentClient('seller-id','seller-key')
    monkeypatch.setattr(client.session,'request',lambda *args,**kwargs:pytest.fail('No advertising write may be sent'))
    args=('101',500) if operation=='set_campaign_budget' else ('101',)
    with pytest.raises(NotImplementedError,match='не подключены'):getattr(client,operation)(*args,dry_run=dry_run)
def test_seller_prices_keep_their_credentials_and_price_dry_run(monkeypatch):
    client=OzonAgentClient('seller-id','seller-key');calls=[]
    class Response:
        status_code=200;content=b'json';headers={}
        def json(self):return {'items':[{'offer_id':'sku-1','price':{'price':'100.00'}}],'cursor':''}
    def request(method,url,**kwargs):calls.append((method,url,kwargs));return Response()
    monkeypatch.setattr(client.session,'request',request)
    rows=client.get_prices();assert rows[0].sku=='sku-1' and rows[0].current_price==100
    assert len(calls)==1 and calls[0][1]=='https://api-seller.ozon.ru/v5/product/info/prices'
    assert calls[0][2]['headers']['Client-Id']=='seller-id' and calls[0][2]['headers']['Api-Key']=='seller-key'
    assert client.set_price('sku-1',101,dry_run=True).dry_run and len(calls)==1
