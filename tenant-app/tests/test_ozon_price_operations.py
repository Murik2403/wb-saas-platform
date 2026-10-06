import sys,json
from pathlib import Path
import pytest,requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import config
import ozon_price_operations as p
@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DB_PATH',tmp_path/'data.sqlite');monkeypatch.setattr(config,'get_ozon_credentials',lambda:('seller','secret'))
    rows=[{'product_id':'1','offer_id':'SKU','currency_code':'RUB','price':'123.45','old_price':None,'min_price':None}]
    class Client:
        writes=[]
        def prices(self):return rows.copy()
        def send_once(self,value):self.writes.append(value);return {'result':[{'product_id':1,'offer_id':'SKU','updated':True,'errors':[]}]}
    client=Client();monkeypatch.setattr(p,'client',lambda credentials:client);return client,rows

def prepared(env):return p.refresh()['source_fingerprint'],[{'product_id':'1','price':'125.67'}]
def test_read_never_remote_and_unknown(env,monkeypatch):
    monkeypatch.setattr(p,'client',lambda _:pytest.fail('GET upstream forbidden'))
    assert p.read()['loaded'] is False and p.read()['source_fingerprint'] is None
@pytest.mark.parametrize('price',['0','-1','NaN','Infinity','1000000000.01','1.001',1.23,True])
def test_invalid_prices(env,price):
    fp,rows=prepared(env);rows[0]['price']=price
    with pytest.raises(p.PriceError):p.preview(rows,fp)
    assert not env[0].writes

def test_preview_exact_and_local(env):
    fp,rows=prepared(env);assert p.preview(rows,fp)['items'][0]['price']=='125.67';assert not env[0].writes

def test_stale_no_write(env):
    fp,rows=prepared(env);env[1][0]['price']='120.00'
    result=p.confirm(rows,fp,'unique-request-key-1',True);assert result['status']=='stale';assert not env[0].writes

def test_confirm_single_identity_single_attempt_idempotent(env):
    fp,rows=prepared(env);result=p.confirm(rows,fp,'unique-request-key-2',True)
    assert result['status']=='completed';assert env[0].writes==[[{'product_id':1,'price':'125.67','currency_code':'RUB'}]]
    assert p.confirm(rows,fp,'unique-request-key-2',True)==result
    assert len(env[0].writes)==1
    with pytest.raises(p.PriceError):p.confirm([{'product_id':'1','price':'127.00'}],fp,'unique-request-key-2',True)

def test_partial_per_sku(env,monkeypatch):
    fp,rows=prepared(env);monkeypatch.setattr(env[0],'send_once',lambda _: {'result':[{'product_id':1,'updated':False,'errors':[{'code':'BAD_PRICE','message':'private upstream'}]}]})
    result=p.confirm(rows,fp,'unique-request-key-3',True);assert result['status']=='partial' and result['items'][0]['errors']==[{'code':'BAD_PRICE'}];assert 'private upstream' not in json.dumps(result)
@pytest.mark.parametrize('code',['unknown_outcome','rate_limit'])
def test_ambiguous_or_429_is_terminal_no_retry(env,monkeypatch,code):
    fp,rows=prepared(env);calls=[]
    def once(_):calls.append(1);raise p.PriceError('No retry',code,429 if code=='rate_limit' else 409)
    monkeypatch.setattr(env[0],'send_once',once)
    first=p.confirm(rows,fp,'unique-request-key-4',True)
    assert first['status']==code and p.confirm(rows,fp,'unique-request-key-4',True)==first and len(calls)==1

def test_unconfirmed_and_account_switch(env,monkeypatch):
    fp,rows=prepared(env)
    with pytest.raises(p.PriceError):p.confirm(rows,fp,'unique-request-key-5',False)
    monkeypatch.setattr(config,'get_ozon_credentials',lambda:('different-seller','secret'))
    assert not p.read()['loaded']
    with pytest.raises(p.PriceError):p.preview(rows,fp)

def test_identical_prices_other_seller_cannot_replay_old_draft(env,monkeypatch):
    old,rows=prepared(env)
    monkeypatch.setattr(config,'get_ozon_credentials',lambda:('other-seller','other-key'))
    new=p.refresh()['source_fingerprint']
    assert new!=old
    with pytest.raises(p.PriceError) as caught:p.confirm(rows,old,'seller-bound-request',True)
    assert caught.value.code=='stale' and not env[0].writes

def test_price_transport_precision_and_no_redirect_no_retry(monkeypatch):
    class Response:
        status_code=200
        def iter_content(self,chunk_size):yield b'{"items":[{"product_id":1,"offer_id":"sku","price":{"price":999999999.99,"currency_code":"RUB"}}],"cursor":""}'
        def close(self):pass
    class Session:
        calls=[]
        def post(self,url,**kwargs):self.calls.append((url,kwargs));return Response()
    session=Session();client=p.PriceClient('id','secret',session=session)
    assert client.prices()[0]['price']=='999999999.99';assert session.calls[0][1]['allow_redirects'] is False

def test_mutation_timeout_is_unknown_no_retry():
    class Session:
        calls=0
        def post(self,*a,**k):self.calls+=1;raise requests.Timeout()
    session=Session();client=p.PriceClient('id','secret',session=session)
    with pytest.raises(p.PriceError) as caught:client.send_once([])
    assert caught.value.code=='unknown_outcome' and session.calls==1

def test_unknown_result_rows_never_success(env,monkeypatch):
    fp,rows=prepared(env);monkeypatch.setattr(env[0],'send_once',lambda _: {'result':[]})
    assert p.confirm(rows,fp,'unique-request-key-6',True)['status']=='unknown_outcome'
