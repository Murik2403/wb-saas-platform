import pytest
from ozon_accounting_client import OzonAccountingClient,OzonAccountingError
from .test_ozon_accounting import Session


def test_official_return_string_id_becomes_canonical_numeric_request_cursor():
    session=Session([{'returns':[{'id':'1000015552'}],'has_next':True},{'returns':[],'has_next':False}])
    client=OzonAccountingClient('synthetic','synthetic',session=session)
    assert client._pages('/v1/returns/list',{'limit':500},rows='returns',cursor='last_id',has_next=True,numeric=True)==[{'id':'1000015552'}]
    assert session.calls[1][1]['last_id']==1000015552 and type(session.calls[1][1]['last_id']) is int

@pytest.mark.parametrize('identifier',['01','-1','+1','1.0','９','9223372036854775808',True,0])
def test_return_cursor_does_not_accept_noncanonical_or_out_of_int64_identifiers(identifier):
    session=Session([{'returns':[{'id':identifier}],'has_next':True}])
    client=OzonAccountingClient('synthetic','synthetic',session=session)
    with pytest.raises(OzonAccountingError):client._pages('/v1/returns/list',{'limit':500},rows='returns',cursor='last_id',has_next=True,numeric=True)


def test_current_posting_request_contract_and_top_level_cursor_pagination():
    pages=[{'result':{'items':[],'last_id':''}},{'items':[],'cursor':''},
           {'postings':[{'posting_number':'FBO1'}],'cursor':'next','has_next':True},
           {'postings':[],'cursor':'','has_next':False},{'postings':[],'cursor':'','has_next':False},
           {'returns':[],'has_next':False},{'accrual_types':[]},{'accruals':[],'last_id':''}]
    session=Session(pages)
    client=OzonAccountingClient('synthetic','synthetic',session=session)
    client.finance_postings=lambda _:[]
    source=client.snapshot('2026-09-01','2026-09-01')
    assert len(source['fbo'])==1
    calls=[(url,body) for url,body in session.calls if '/posting/' in url]
    assert [body['cursor'] for _,body in calls]==['','next','']
    for _,body in calls:assert body['limit']==100 and body['sort_dir']=='ASC' and 'dir' not in body

def test_catalog_details_use_explicit_product_id_and_literal_sku_not_id():
    session=Session([{'items':[{'id':987,'sku':123,'offer_id':'Synthetic','name':'Synthetic','price':'250.25','currency_code':'RUB'}]}])
    client=OzonAccountingClient('synthetic','synthetic',session=session)
    values=client.product_details([{'product_id':987,'offer_id':'Synthetic'}])
    assert session.calls[0][1]=={'product_id':['987']}
    assert values[0]['sku']==123 and values[0]['product_id']==987

def test_unreturned_detail_does_not_invent_sku_and_mismatched_detail_is_rejected():
    client=OzonAccountingClient('synthetic','synthetic',session=Session([{'items':[]}]))
    assert 'sku' not in client.product_details([{'product_id':987}])[0]
    client=OzonAccountingClient('synthetic','synthetic',session=Session([{'items':[{'id':988,'sku':123}]}]))
    with pytest.raises(OzonAccountingError):client.product_details([{'product_id':987}])

def test_current_price_source_paginates_raw_currency_no_scale():
    session=Session([{'items':[{'product_id':987,'price':{'price':250.25,'currency_code':'RUB'}}],'cursor':'next','total_items':1}, {'items':[],'cursor':'','total_items':1}])
    client=OzonAccountingClient('synthetic','synthetic',session=session)
    assert client.product_prices()[0]['price']['price']=='250.25'
    assert session.calls[0][1]=={'filter':{'visibility':'ALL'},'limit':1000,'cursor':''}
    assert session.calls[1][1]['cursor']=='next'
