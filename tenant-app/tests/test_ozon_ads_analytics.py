import io,json,zipfile
from decimal import Decimal
import pytest
import requests
from cryptography.fernet import Fernet
import config
import db.core as core
import ozon_ads_analytics as ads

START='2026-09-01';END='2026-09-02';REMOTE='12345678-1234-4234-9234-123456789012'
def csv_report(cid='101',values=('100','10','25,00','2','100,00'),daily=False,totals=True):
    fields=['sku','Показы','Клики','Расход, ₽, с НДС','Заказы','Продажи, ₽']+(['Дата'] if daily else [])
    row=['1',*values]+(['01.09.2026'] if daily else [])
    lines=[f'Кампания по продвижению товаров № {cid}, период 01.09.2026-02.09.2026',';'.join(fields),';'.join(row)]
    if totals:lines+=[';'.join(['Всего',*values]+([''] if daily else []))]
    return ('\n'.join(lines)).encode()
def zipped(*reports):
    output=io.BytesIO()
    with zipfile.ZipFile(output,'w') as archive:
        for i,report in enumerate(reports):archive.writestr(str(i)+'.csv',report)
    return output.getvalue()
@pytest.fixture
def env(tmp_path,monkeypatch):
    monkeypatch.setattr(config,'DATA_DIR',tmp_path);monkeypatch.setattr(config,'DATA_ENCRYPTION_KEY',Fernet.generate_key().decode());monkeypatch.setattr(core,'DB_PATH',tmp_path/'db.sqlite')
    return tmp_path
def test_credentials_separate_encrypted_and_no_seller_mutation(env):
    seller=env/'.ozon_credentials';seller.write_text('existing-seller')
    assert ads.save_credentials('performance-id','private-secret')=={'configured':True}
    raw=ads.credential_path().read_text();assert raw.startswith('enc:v1:') and 'private-secret' not in raw
    assert ads.credentials()=={'client_id':'performance-id','client_secret':'private-secret'}
    assert seller.read_text()=='existing-seller'
    ads.delete_credentials();assert ads.credentials() is None and seller.exists()
def test_missing_encryption_key_fails_closed(env,monkeypatch):
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEY','')
    with pytest.raises(RuntimeError):ads.save_credentials('id','secret')
    assert not ads.credential_path().exists()
def test_legacy_or_wrong_key_credentials_are_not_returned(env,monkeypatch):
    ads.credential_path().write_text('plaintext-key')
    with pytest.raises(ads.PerformanceError):ads.credentials()
    ads.credential_path().unlink();ads.save_credentials('id','secret')
    monkeypatch.setattr(config,'DATA_ENCRYPTION_KEY',Fernet.generate_key().decode())
    with pytest.raises(RuntimeError):ads.credentials()
def test_missing_snapshot_is_unknown_not_zero(env):
    result=ads.analytics(START,END)
    assert not result['loaded'] and not result['source_complete']
    assert all(value is None for value in result['totals'].values())
def test_csv_total_is_not_double_counted_and_derived_metrics_correct():
    rows=ads.parse_report(csv_report(),START,END,['101']);assert len(rows)==1
    value=ads.metrics(rows)
    assert {k:value[k] for k in ('views','clicks','spend','orders','ad_revenue','ctr','cpc','cpm','cpo','drr')}=={'views':100,'clicks':10,'spend':'25.00','orders':2,'ad_revenue':'100.00','ctr':10.0,'cpc':'2.50','cpm':'250.00','cpo':'12.50','drr':25.0}
    assert value['model_orders'] is None
def test_official_whole_line_quoted_cp1251_format():
    original=csv_report().decode().replace('₽','?').splitlines();report='\r\r\n'.join([original[0]]+['"'+x+'"' for x in original[1:]]).encode('cp1251')
    assert ads.metrics(ads.parse_report(report,START,END,['101']))['spend']=='25.00'
def test_daily_date_first_and_total_label_are_not_parsed_as_a_date():
    report='Кампания по продвижению товаров № 101, период 01.09.2026-02.09.2026\nДата;Показы;Клики;Расход;Заказы;Продажи\n01.09.2026;10;2;1.10;1;10.00\nВсего;10;2;1.10;1;10.00'.encode()
    rows=ads.parse_report(report,START,END,['101']);assert len(rows)==1 and rows[0]['day']==START
    assert ads.metrics(rows)['spend']=='1.10'
    with pytest.raises(ads.PerformanceError,match='не совпадает'):
        ads.parse_report(report.decode().replace('Всего;10;2;1.10','Всего;10;2;9.99').encode(),START,END,['101'])
def test_blank_metric_and_zero_denominator_stay_unknown():
    result=ads.metrics(ads.parse_report(csv_report(values=('0','0','','0','0')),START,END,['101']))
    assert result['views']==0 and result['spend'] is None
    assert all(result[k] is None for k in ('ctr','cpc','cpm','cpo','drr'))
@pytest.mark.parametrize('values',[('NaN','1','1','1','1'),('-1','1','1','1','1'),('1','1.5','1','1','1'),('1','1','Infinity','1','1')])
def test_invalid_numbers_reject_report(values):
    with pytest.raises(ads.PerformanceError):ads.parse_report(csv_report(values=values),START,END,['101'])
def test_selected_campaign_completeness_and_duplicate_parts():
    report=zipped(csv_report('101'),csv_report('102'))
    rows=ads.parse_report(report,START,END,['101','102']);assert len(rows)==2
    with pytest.raises(ads.PerformanceError):ads.parse_report(report,START,END,['101','102','103'])
    with pytest.raises(ads.PerformanceError):ads.parse_report(zipped(csv_report(),csv_report()),START,END,['101'])
@pytest.mark.parametrize('report',[csv_report('999'),csv_report().replace(b'01.09.2026-02.09.2026',b'01.08.2026-02.08.2026'),b''])
def test_wrong_campaign_period_or_empty_report_rejected(report):
    with pytest.raises(ads.PerformanceError):ads.parse_report(report,START,END,['101'])
def test_snapshots_daily_and_missing_cell_coverage(env):
    job={'start_date':START,'end_date':END,'campaign_ids':'["101"]'}
    ads.save_snapshot(job,ads.parse_report(csv_report(daily=True),START,END,['101']))
    result=ads.analytics(START,END);assert result['by_day_available'] and len(result['by_day'])==1 and result['daily_scope']=='reported_days'
    ads.save_snapshot(job,ads.parse_report(csv_report(values=('100','10','','2','100')),START,END,['101']))
    result=ads.analytics(START,END);assert not result['source_complete'] and result['source_received_complete'];assert not result['by_day_available'] and result['totals']['spend'] is None
    assert result['coverage']['spend']=={'known_rows':0,'total_rows':1}
class FakeResponse:
    def __init__(self,obj=None,status=200,body=None):self.body=json.dumps(obj).encode() if body is None else body;self.status_code=status;self.closed=False
    def iter_content(self,chunk_size):yield self.body
    def close(self):self.closed=True
class Session:
    def __init__(self,*responses):self.responses=list(responses);self.calls=[]
    def request(self,*args,**kwargs):
        self.calls.append((args,kwargs));value=self.responses.pop(0)
        if isinstance(value,Exception):raise value
        return value
def token(value='synthetic-token',expires=1800):return FakeResponse({'access_token':value,'expires_in':expires,'token_type':'Bearer'})
def test_oauth_expiry_and_401_refresh_are_bounded_and_never_use_seller_headers():
    session=Session(token(),FakeResponse({},401),token('renewed'),FakeResponse({'UUID':REMOTE}));client=ads.PerformanceClient('perf','secret',session=session)
    assert client.submit(['101'],START,END)==REMOTE and len(session.calls)==4
    for (method,url),kwargs in session.calls:
        assert url.startswith(ads.PERFORMANCE_URL) and kwargs['allow_redirects'] is False
        assert 'Api-Key' not in kwargs['headers'] and 'Client-Id' not in kwargs['headers']
    assert session.calls[-1][1]['json']['groupBy']=='DATE'
    session=Session(token(expires=2),FakeResponse({'state':'IN_PROGRESS'}),token(),FakeResponse({'state':'OK'}));clock=[0];client=ads.PerformanceClient('id','secret',session=session,clock=lambda:clock[0])
    client.status(REMOTE);clock[0]=3;client.status(REMOTE);assert len(session.calls)==4
@pytest.mark.parametrize('response',[FakeResponse({},429),requests.Timeout('secret-in-url'),FakeResponse({'error':'secret'},403),FakeResponse({},302)])
def test_remote_errors_are_bounded_sanitized_and_do_not_sleep(response,monkeypatch):
    monkeypatch.setattr(ads.time,'sleep',lambda *args:pytest.fail('must not retry/sleep'))
    session=Session(response);client=ads.PerformanceClient('id','secret',session=session)
    with pytest.raises(ads.PerformanceError) as caught:client.status(REMOTE)
    assert 'secret' not in str(caught.value) and len(session.calls)==1
class Reports:
    def __init__(self,state='OK',body=None,error=None):self.state=state;self.body=body or csv_report();self.error=error;self.downloads=0
    def submit(self,*args):return REMOTE
    def status(self,*args):
        if self.error:raise self.error
        return {'state':self.state}
    def report(self,*args):self.downloads+=1;return self.body
def test_async_pending_then_complete_idempotent_and_no_snapshot_before_ready(env):
    value={'client_id':'id','client_secret':'secret'};client=Reports(state='IN_PROGRESS');job=ads.start_report(client,value,START,END,['101'])
    assert ads.poll_report(client,value,job['id'])['status']=='pending' and not ads.analytics(START,END)['loaded'] and client.downloads==0
    client.state='OK';assert ads.poll_report(client,value,job['id'])['status']=='complete'
    revision=ads.analytics(START,END)['revision'];ads.poll_report(client,value,job['id'])
    assert client.downloads==1 and ads.analytics(START,END)['revision']==revision
def test_429_partial_failure_and_unknown_status_keep_previous_snapshot(env):
    value={'client_id':'id','client_secret':'secret'};good=Reports();job=ads.start_report(good,value,START,END,['101']);ads.poll_report(good,value,job['id']);revision=ads.analytics(START,END)['revision']
    retry=Reports(error=ads.PerformanceError('rate_limit','later',True));job=ads.start_report(retry,value,START,END,['101']);assert ads.poll_report(retry,value,job['id'])['status']=='pending'
    incomplete=Reports(body=csv_report('102'));job=ads.start_report(incomplete,value,START,END,['101']);assert ads.poll_report(incomplete,value,job['id'])['status']=='failed'
    unknown=Reports(state='READY');job=ads.start_report(unknown,value,START,END,['101']);assert ads.poll_report(unknown,value,job['id'])['status']=='failed'
    assert ads.analytics(START,END)['revision']==revision
def test_credentials_change_invalidates_old_pending_jobs_and_old_metrics(env):
    ads.save_credentials('id','secret');old=ads.credentials();job=ads.start_report(Reports(),old,START,END,['101']);ads.poll_report(Reports(),old,job['id'])
    pending=ads.start_report(Reports(),old,START,END,['101']);ads.save_credentials('new-account','new-secret')
    assert ads.report_job(pending['id'])['status']=='cancelled' and not ads.analytics(START,END)['loaded']
def test_expired_poll_cap_and_scope_never_download(env):
    value={'client_id':'id','client_secret':'secret'};client=Reports();job=ads.start_report(client,value,START,END,['101'])
    with pytest.raises(ValueError):ads.poll_report(client,{'client_id':'other','client_secret':'other'},job['id'])
    with core.connect() as conn:conn.execute('UPDATE ozon_ads_reports SET polls=?',(ads.MAX_POLLS,))
    assert ads.poll_report(client,value,job['id'])['status']=='expired' and client.downloads==0
@pytest.mark.parametrize('ids',[[],['101']*2,['1']*11,['0'],['invalid']])
def test_campaign_bounds_before_remote(env,ids):
    class Never:
        def submit(self,*args):pytest.fail('invalid payload must not call upstream')
    with pytest.raises(ValueError):ads.start_report(Never(),{},START,END,ids)
def test_period_bounds_and_csv_unknown_cells(env):
    with pytest.raises(ValueError):ads.period('2026-01-01','2026-04-01')
    job={'start_date':START,'end_date':END,'campaign_ids':'["101"]'};ads.save_snapshot(job,ads.parse_report(csv_report(values=('100','10','','2','100')),START,END,['101']))
    text=ads.export_csv(START,END);assert '101;100;10;;2;' in text and 'None' not in text
def test_large_aggregate_money_and_csv_preserve_cents(env):
    base=ads.parse_report(csv_report(values=('1','1','999999999999.99','1','999999999999.99')),START,END,['101'])[0]
    small={**base,'spend':'0.01','ad_revenue':'0.01'}
    rows=[base]*9008+[small]
    expected='9007999999999909.93'
    assert ads.metrics(rows)['spend']==expected and ads.metrics(rows)['ad_revenue']==expected
    ads.save_snapshot({'start_date':START,'end_date':END,'campaign_ids':'["101"]'},rows)
    assert ads.analytics(START,END)['totals']['spend']==expected and expected in ads.export_csv(START,END)
def test_size_and_zip_part_bounds_are_enforced_before_publication(monkeypatch):
    monkeypatch.setattr(ads,'MAX_REPORT_BYTES',32)
    response=FakeResponse(body=b'x'*33);client=ads.PerformanceClient('id','secret',session=Session(response))
    with pytest.raises(ads.PerformanceError) as caught:client.status(REMOTE)
    assert caught.value.code=='report_limit' and response.closed
    with pytest.raises(ads.PerformanceError):ads.parse_report(b'x'*33,START,END,['101'])
def test_complete_zip_checks_every_campaign_not_only_first_file():
    ids=[str(x) for x in range(101,111)];rows=ads.parse_report(zipped(*(csv_report(cid) for cid in ids)),START,END,ids)
    assert len(rows)==10 and {r['campaign_id'] for r in rows}==set(ids)
    with pytest.raises(ads.PerformanceError):ads.parse_report(zipped(*(csv_report(str(x)) for x in range(101,112))),START,END,ids)
def test_tenant_database_credentials_and_jobs_do_not_cross_scope(env,monkeypatch):
    ads.save_credentials('tenant-a-performance','a-secret');value=ads.credentials();job=ads.start_report(Reports(),value,START,END,['101']);ads.poll_report(Reports(),value,job['id'])
    other=env/'tenant-b';other.mkdir();monkeypatch.setattr(config,'DATA_DIR',other);monkeypatch.setattr(config,'DATA_ENCRYPTION_KEY',Fernet.generate_key().decode());monkeypatch.setattr(core,'DB_PATH',other/'db.sqlite')
    assert ads.credentials() is None and not ads.analytics(START,END)['loaded']
    with pytest.raises(ValueError):ads.report_job(job['id'])
def test_report_status_kind_and_original_request_are_checked(env):
    class Different(Reports):
        def status(self,*args):return {'state':'OK','kind':'VIDEO','link':'https://attacker.invalid/credentials'}
    value={'client_id':'id','client_secret':'secret'};client=Different();job=ads.start_report(client,value,START,END,['101'])
    assert ads.poll_report(client,value,job['id'])['status']=='failed' and client.downloads==0
    class WrongScope(Reports):
        def status(self,*args):return {'state':'OK','kind':'STATS','request':{'campaigns':['999'],'dateFrom':START,'dateTo':END,'groupBy':'DATE'}}
    client=WrongScope();job=ads.start_report(client,value,START,END,['101']);assert ads.poll_report(client,value,job['id'])['status']=='failed' and client.downloads==0
def test_foreign_currency_headers_are_never_interpreted_as_rubles():
    report=csv_report().decode().replace('Расход, ₽, с НДС','Расход, USD')
    with pytest.raises(ads.PerformanceError) as caught:ads.parse_report(report.encode(),START,END,['101'])
    assert caught.value.code=='currency'
