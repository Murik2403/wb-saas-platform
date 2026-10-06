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

def _insert_pending(key,age,account=None):
    import time
    acct=account or p.identity()[0]
    with p.connection() as db:db.execute("INSERT INTO ozon_price_operation (request_key,account,draft_hash,status,payload,actor,created_at) VALUES (?,?,?,?,?,?,?)",(key,acct,'x','in_progress',json.dumps({'request_key':key,'status':'in_progress','items':[],'automatic_retry':False}),'',time.time()-age))

def test_fresh_in_progress_blocks_new_send(env):
    fp,rows=prepared(env);_insert_pending('fresh-pending-key-0001',30)
    with pytest.raises(p.PriceError) as caught:p.confirm(rows,fp,'unique-request-key-6',True)
    assert caught.value.code=='operation_pending' and not env[0].writes

def test_stale_in_progress_becomes_unknown_and_does_not_block(env):
    fp,rows=prepared(env);_insert_pending('stale-pending-key-0001',p.PENDING_TTL_SECONDS+5)
    assert p.confirm(rows,fp,'unique-request-key-7',True)['status']=='completed' and len(env[0].writes)==1
    # replaying the abandoned key reports unknown_outcome, never a running send
    with p.connection() as db:assert db.execute("SELECT status FROM ozon_price_operation WHERE request_key='stale-pending-key-0001'").fetchone()[0]=='unknown_outcome'

def test_legacy_table_without_created_at_is_migrated(env):
    import sqlite3
    db=sqlite3.connect(p.config.DB_PATH);db.executescript("CREATE TABLE ozon_price_operation (request_key TEXT PRIMARY KEY,account TEXT NOT NULL,draft_hash TEXT NOT NULL,status TEXT NOT NULL,payload TEXT NOT NULL,actor TEXT NOT NULL DEFAULT '');INSERT INTO ozon_price_operation VALUES ('legacy-key-0000000001','a','h','in_progress','{}','');");db.commit();db.close()
    with p.connection() as db:assert db.execute("SELECT created_at FROM ozon_price_operation").fetchone()[0]==0

def test_large_price_change_needs_explicit_allowance(env):
    fp,rows=prepared(env);rows[0]['price']='300.00'
    assert p.preview(rows,fp)['large_changes']==['1']
    with pytest.raises(p.PriceError) as caught:p.confirm(rows,fp,'unique-request-key-8',True)
    assert caught.value.code=='price_change_too_large' and not env[0].writes
    assert p.confirm(rows,fp,'unique-request-key-8',True,allow_large_change=True)['status']=='completed'

def test_boundary_70_percent_change_is_not_large(env):
    fp,rows=prepared(env);rows[0]['price']='209.86'
    assert p.preview(rows,fp)['large_changes']==[]
