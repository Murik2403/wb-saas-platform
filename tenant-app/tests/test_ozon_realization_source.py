import copy
import sys
from pathlib import Path
import pytest
import requests
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
sys.path.insert(0,str(Path(__file__).resolve().parent))
from base import DbTestCase
from db.core import connect
from db.ozon_realization_source import preview,save,saved
from db.ozon_accounting import import_snapshot,workspace
from ozon_accounting_client import OzonAccountingClient,checked_realization_month,OzonAccountingError
from test_ozon_accounting import Session,fixture_snapshot


def source():
    row={'row_number':1,'item':{'sku':123,'name':'Synthetic'},'order':{'posting_number':'synthetic-order'},'delivery_commission':{'quantity':2,'total':'100.01'},'return_commission':{'quantity':1,'total':'-25.01'}}
    return {'source_kind':'ozon-realization-v1','month':9,'year':2026,'report':{'header':{'number':'synthetic-document','doc_date':'2026-10-01','start_date':'2026-09-01','stop_date':'2026-09-30','currency_sys_name':'RUB','payer_name':'PRIVATE_NOT_DISPLAYED'},'rows':[row,copy.deepcopy(row)]},'page_receipts':[]}


def test_monthly_read_is_one_bounded_attempt_not_report_generation():
    class S(Session):
        def post(self,url,**kwargs):
            assert kwargs['timeout']==5 and kwargs['allow_redirects'] is False and kwargs['stream'] is True
            return super().post(url,**kwargs)
    session=S([source()['report']]); client=OzonAccountingClient('fake','fake',session=session)
    result=client.realization_source(9,2026)
    assert session.calls==[(client.BASE+'/v1/finance/realization/posting',{'month':9,'year':2026})]
    assert len(result['report']['rows'])==2 and len(result['page_receipts'])==1
    assert len(result['page_receipts'][0]['response_fingerprint'])==64


@pytest.mark.parametrize('month,year',[(0,2026),(True,2026),(9,True),(7,2023),(1,3000)])
def test_bad_month_has_no_remote_call(month,year):
    client=OzonAccountingClient('fake','fake',session=Session([]))
    with pytest.raises(ValueError): client.realization_source(month,year)
    assert client.requests==0


def test_timeout_never_retries_and_row_byte_deadline_budgets_apply():
    class Timeout:
        calls=0
        def post(self,*args,**kwargs): self.calls+=1;raise requests.Timeout()
    session=Timeout(); client=OzonAccountingClient('fake','fake',session=session)
    with pytest.raises(OzonAccountingError): client.realization_source(9,2026)
    assert session.calls==1
    for client in (OzonAccountingClient('f','f',session=Session([source()['report']]),max_rows=1),OzonAccountingClient('f','f',session=Session([source()['report']]))):
        if client.max_rows!=1: client.byte_budget=1
        with pytest.raises(OzonAccountingError):client.realization_source(9,2026)
    expired=OzonAccountingClient('f','f',session=Session([]));expired.deadline=0
    with pytest.raises(OzonAccountingError):expired.realization_source(9,2026)
    assert expired.session.calls==[]


@pytest.mark.parametrize('field,value',[('currency_sys_name','KZT'),('currency_sys_name',{}),('start_date','2026-08-01'),('stop_date','2026-10-01'),('number',{}),('doc_date','2026-02-30')])
def test_header_wrong_period_currency_or_identity_rejected(field,value):
    raw=source();raw['report']['header'][field]=value
    with pytest.raises(ValueError):preview(raw)


@pytest.mark.parametrize('qty,total',[(None,None),(True,True),('2','NaN'),(2**31,'0.001'),({},{}),(1,'Infinity')])
def test_unsafe_quantity_money_unknown_without_rewriting_source(qty,total):
    raw=source();raw['report']['rows'][0]['delivery_commission']={'quantity':qty,'total':total}
    before=copy.deepcopy(raw);result=preview(raw)
    assert result['rows'][0]['delivery_commission']=={'quantity_text':None if type(qty)is not int or not -(2**31)<=qty<2**31 else str(qty),'total_rub':None}
    assert result['rows'][0]['reason'] and raw==before


def test_duplicate_source_occurrences_exact_signed_totals_and_private_fields_not_exposed():
    raw=source();result=preview(raw)
    assert len({r['source_occurrence_key'] for r in result['rows']})==2
    assert result['rows'][0]['return_commission']['total_rub']=='-25.01'
    assert 'PRIVATE_NOT_DISPLAYED' not in str(result)
    assert all(result[k] is False for k in ('financial_normalization_verified','marketplace_source_complete','movement_verified','correction_identity_verified','ledger_confirmation_allowed'))
    raw['report']['rows']*=60
    assert preview(raw)['source_row_count']==120 and len(preview(raw)['rows'])==100


def test_row_metadata_containers_are_unknown_and_no_private_fields_are_forwarded():
    raw=source();raw['report']['rows'][0].update(row_number={},item={'sku':{},'name':'PRIVATE'},order={'posting_number':{},'created_date':'PRIVATE'},legal_entity_document={'number':'PRIVATE'})
    result=preview(raw)['rows'][0]
    assert result['row_number_text'] is None and result['sku_text'] is None and result['posting_number'] is None
    assert 'PRIVATE' not in str(result)


def test_partial_header_scope_is_explicit_not_full_month_coverage():
    raw=source();raw['report']['header'].update(start_date='2026-09-10',stop_date='2026-09-20')
    result=preview(raw)
    assert result['period']=={'start_date':'2026-09-10','end_date':'2026-09-20'}
    assert result['partial_period'] is True and result['source_complete'] is False
    assert preview(source())['partial_period'] is False


def test_stream_deadline_is_checked_after_response_and_response_closed():
    class SlowResponse:
        status_code=200
        closed=False
        def iter_content(self,chunk_size):
            client.deadline=0
            yield b'{}'
        def close(self):self.closed=True
    response=SlowResponse()
    class S:
        calls=0
        def post(self,*args,**kwargs):self.calls+=1;return response
    session=S();client=OzonAccountingClient('f','f',session=session)
    with pytest.raises(OzonAccountingError):client.realization_source(9,2026)
    assert response.closed and session.calls==1 and client.page_receipts==[]


@pytest.mark.parametrize('status',[403,404])
def test_unavailable_document_is_not_zero_activity_and_never_retries(status):
    from test_ozon_accounting import Response
    response=Response({});response.status_code=status
    class S:
        calls=0
        def post(self,*args,**kwargs):self.calls+=1;return response
    session=S();client=OzonAccountingClient('f','f',session=session)
    with pytest.raises(OzonAccountingError,match='не подтверждение нулевых продаж'):
        client.realization_source(9,2026)
    assert session.calls==1 and client.page_receipts==[]


class PersistenceTests(DbTestCase):
    def test_replay_changed_versions_and_no_daily_document_or_wb_changes(self):
        import_snapshot(fixture_snapshot()); daily=workspace('2026-09-01','2026-09-02')
        with connect() as conn: before=[tuple(r) for r in conn.execute('SELECT * FROM sales')]
        raw=source();first=save(raw);assert save(raw)==first
        raw['report']['rows'][0]['delivery_commission']['total']='101.01';second=save(raw)
        assert second['source_version']!=first['source_version'] and saved(9,2026)==second
        invalid=source();invalid['report']['header']['currency_sys_name']='KZT'
        with pytest.raises(ValueError):save(invalid)
        assert saved(9,2026)==second and workspace('2026-09-01','2026-09-02')==daily
        with connect() as conn:
            assert conn.execute('SELECT COUNT(*) FROM ozon_document_events').fetchone()[0]==0
            assert conn.execute('SELECT COUNT(*) FROM ozon_accrual_ledger').fetchone()[0]==1
            assert conn.execute('SELECT COUNT(*) FROM ozon_raw_source_versions').fetchone()[0]==3
            assert [tuple(r) for r in conn.execute('SELECT * FROM sales')]==before
