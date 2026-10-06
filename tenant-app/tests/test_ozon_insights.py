import csv,io,json,uuid
from datetime import date
import pytest
from db import core
from db.ozon_accounting import import_snapshot
from db.ozon_pnl import COLUMNS,preview_import,confirm_import,add_cost_lot
import ozon_insights as insights

@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DB_PATH',tmp_path/'insights.sqlite');core.init_db()
    return core.DB_PATH

def snapshot(**extra):
    return {'start_date':'2026-09-01','end_date':'2026-09-30','products':[{'sku':123,'offer_id':'synthetic'}], 'stocks':[], 'fbo':[], 'fbs':[], 'returns':[], 'accruals':[], 'types':[], **extra}

def documents(namespace='main',cost=True):
    row=dict(external_event_id='one',accrued_at='2026-09-04T12:00:00+03:00',movement_at='2026-09-03T12:00:00+03:00',kind='sale',sku='123',posting_ref='synthetic-post',return_of='',quantity='2',revenue_rub='100.01',commission_rub='-10.00',logistics_rub='-5.00',other_rub='0.00',source_total_rub='85.01',currency='RUB',document_ref='Synthetic')
    out=io.StringIO();w=csv.DictWriter(out,fieldnames=COLUMNS);w.writeheader();w.writerow(row)
    p=preview_import(out.getvalue(),namespace);confirm_import(out.getvalue(),namespace,p['fingerprint'],'fixture')
    if cost:add_cost_lot(namespace=namespace,sku='123',received_at='2026-09-01T00:00:00Z',quantity_units=2,unit_cost_rub='10.00',document_ref='Synthetic',reason='fixture',request_key=str(uuid.uuid4()),actor='fixture')


def test_missing_period_never_becomes_zero(db):
    r=insights.products('2026-09-01','2026-09-30')
    assert r['loaded'] is False and r['items']==[] and not r['coverage']['marketplace_complete']
    m=insights.monthly('2026-09-01','2026-09-30')
    assert m['items'][0]['revenue_rub'] is None and m['items'][0]['result_after_fifo_rub'] is None


def test_orders_are_not_sales_and_duplicate_posting_not_accumulated(db):
    posting={'posting_number':'P','created_at':'2026-09-03T22:00:00Z','products':[{'sku':123,'quantity':3}]}
    import_snapshot(snapshot(fbo=[posting,posting]))
    r=insights.products('2026-09-01','2026-09-30')['items'][0]
    assert r['order_units']==3 and r['sales_units'] is None and r['revenue_rub'] is None
    h=insights.heatmap('2026-09-01','2026-09-30',metric='order_units')
    assert h['items'][0]['values'][2]==3 and h['basis']=='posting_created_at_utc'


def test_documents_decimal_fifo_monthly_namespace_and_cache_freshness(db):
    documents(cost=False)
    before=insights.products('2026-09-01','2026-09-30')['items'][0]
    assert before['revenue_rub']=='100.01' and before['historical_cogs_rub'] is None
    add_cost_lot(namespace='main',sku='123',received_at='2026-09-01T00:00:00Z',quantity_units=2,unit_cost_rub='10.00',document_ref='Synthetic',reason='fixture',request_key=str(uuid.uuid4()),actor='fixture')
    after=insights.products('2026-09-01','2026-09-30')['items'][0]
    assert after['result_after_fifo_rub']=='65.01' and after['sales_units']==2
    assert insights.monthly('2026-09-01','2026-09-30')['items'][0]['result_after_fifo_rub']=='65.01'
    assert insights.products('2026-09-01','2026-09-30',namespace='other')['items']==[]


def test_rnp_namespace_plan_delete_and_favorite(db):
    import_snapshot(snapshot())
    insights.save_plan('main','123','2026-09-04',10)
    insights.save_favorite('main','123',True)
    r=insights.rnp('2026-09-01','2026-09-30')['items'][0]
    assert r['plan_units']==10 and r['fact_units'] is None and r['favorite'] is True
    assert insights.rnp('2026-09-01','2026-09-30',namespace='other')['items'][0]['plan_units'] is None
    insights.save_plan('main','123','2026-09-04',None)
    assert insights.rnp('2026-09-01','2026-09-30')['items'][0]['plan_units'] is None
    with pytest.raises(ValueError):insights.save_plan('main','999','2026-09-04',1)


def test_tax_never_infers_from_accrual_or_documents(db):
    documents()
    r=insights.tax_input('2026-09-01','2026-09-30')
    assert r['income_input_rub'] is None and r['tax_input_confirmed'] is False
    assert r['document_revenue_suggestion_rub']=='100.01'
    r=insights.tax_input('2026-09-01','2026-09-30',options={'month':'2026-09','regime':'ausn_income','received':'100.01','offset':'0','compensation':'0','other_income':'0','refunds':'0'})
    assert r['income_input_rub']=='100.01' and r['estimate']['estimate']=='8.00'


def test_invalid_windows_metrics_unknown_prices_and_conflicting_duplicates(db):
    with pytest.raises(ValueError):insights.products('2026-09-30','2026-09-01')
    with pytest.raises(ValueError):insights.heatmap('2026-09-01','2026-09-30',metric='profit')
    posting={'posting_number':'P','created_at':'2026-09-03T12:00:00Z','products':[{'sku':123,'quantity':3}]}
    import_snapshot(snapshot(fbo=[posting,{**posting,'products':[{'sku':123,'quantity':4}]}]))
    r=insights.products('2026-09-01','2026-09-30')
    assert r['items'][0]['order_units'] is None and r['coverage']['order_parse_complete'] is False


def test_verified_collection_empty_is_observed_zero_not_zero_sales(db):
    import hashlib
    from datetime import timedelta
    from db.ozon_accounting import canonical
    days=[(date(2026,9,1)+timedelta(days=i)).isoformat() for i in range(30)]
    receipts=[{'endpoint':'fixture-other','response_bytes':1,'request_fingerprint':'0'*64,'response_fingerprint':'1'*64} for i in range(6)]
    receipts += [{'endpoint':'/v1/finance/accrual/by-day','response_bytes':1,'request_fingerprint':hashlib.sha256(canonical({'date':day,'last_id':''}).encode()).hexdigest(),'response_fingerprint':'1'*64} for day in days]
    proof={'schema_version':1,'start_date':'2026-09-01','end_date':'2026-09-30','completed_days':days,'terminal_pages_checked':True,'financial_normalization_verified':False,'page_receipts':receipts}
    import_snapshot(snapshot(collection_provenance=proof))
    r=insights.products('2026-09-01','2026-09-30')
    assert r['coverage']['source_collection_complete'] is True
    assert r['items'][0]['order_units']==0 and r['items'][0]['sales_units'] is None
    assert r['items'][0]['result_after_fifo_rub'] is None and not r['coverage']['marketplace_complete']


def test_saved_plan_visible_in_window_without_api_or_documents(db):
    import_snapshot(snapshot())
    insights.save_plan('main','123','2026-10-05',12)
    insights.save_favorite('main','123',True)
    row=insights.rnp('2026-10-01','2026-10-31')['items'][0]
    assert row['plan_units']==12 and row['fact_units'] is None and row['favorite']


def test_snapshot_correction_replaces_order_metrics_and_does_not_retain_cache(db):
    posting={'posting_number':'P','created_at':'2026-09-03T12:00:00Z','products':[{'sku':123,'quantity':3}]}
    import_snapshot(snapshot(fbo=[posting]));assert insights.products('2026-09-01','2026-09-30')['items'][0]['order_units']==3
    import_snapshot(snapshot(fbo=[{**posting,'products':[{'sku':123,'quantity':2}]}]))
    assert insights.products('2026-09-01','2026-09-30')['items'][0]['order_units']==2


def test_resource_limit_checked_before_projection_json_parse(db,monkeypatch):
    documents()
    monkeypatch.setattr(insights,'MAX_BYTES',20)
    with pytest.raises(ValueError):insights.products('2026-09-01','2026-09-30')


def test_only_literal_stock_skus_enable_catalog_and_plans_without_guessed_product_id(db):
    import_snapshot(snapshot(products=[{'product_id':7,'offer_id':'Synthetic-stock'}],stocks=[{'product_id':7,'stocks':[{'sku':555,'source':'fbo','present':3},{'sku':556,'source':'fbs','present':1}]}]))
    result=insights.products('2026-09-01','2026-09-30')
    assert [r['sku'] for r in result['items']]==['555','556']
    assert result['items'][0]['offer_id']=='Synthetic-stock'
    assert all(r['sales_units'] is None for r in result['items'])
    insights.save_plan('main','555','2026-09-01',3)
    with pytest.raises(ValueError):insights.save_plan('main','7','2026-09-01',3)
    with pytest.raises(ValueError):insights.save_favorite('main','999',True)


def test_overlap_revision_hides_stale_orders_without_invalidating_monthly_documents(db):
    documents()
    import_snapshot(snapshot(fbo=[{'posting_number':'P','created_at':'2026-09-03T12:00:00Z','products':[{'sku':123,'quantity':3}]}]))
    assert insights.products('2026-09-01','2026-09-30')['items'][0]['order_units']==3
    import_snapshot(snapshot(start_date='2026-09-10',end_date='2026-09-11'))
    result=insights.products('2026-09-01','2026-09-30')
    assert result['items'][0]['order_units'] is None and result['coverage']['source_snapshot_stale']
    assert insights.monthly('2026-09-01','2026-09-30')['items'][0]['result_after_fifo_rub']=='65.01'
