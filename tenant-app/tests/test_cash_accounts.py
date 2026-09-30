import sqlite3
from concurrent.futures import ThreadPoolExecutor
import pytest
from db import core,cashflow,cash_accounts as cash,cash_statement as statement

@pytest.fixture(autouse=True)
def database(monkeypatch,tmp_path):
    monkeypatch.setattr(core,'DB_PATH',tmp_path/'cash.sqlite'); core.init_db()

def body(key='cash-request-1',**kwargs):
    return dict(request_key=key,kind='movement',direction='inflow',status='planned',planned_date='2026-09-10',actual_date=None,amount='100',currency='RUB',exchange_rate='1',category='other',reference='',**{})|kwargs
def wallet(currency='RUB'): return cash.create_account('Bank',currency)['id']
def csv(*rows): return ';'.join(statement.COLUMNS)+'\n'+'\n'.join(rows)
def count():
    with core.connect() as conn:return conn.execute('SELECT COUNT(*) FROM cash_entries').fetchone()[0]

def test_legacy_fx_is_valuation_not_native_wallet_and_separate_anchors():
    cashflow.create_entry(body(currency='CNY',exchange_rate='13',amount='12.50',status='actual',actual_date='2026-09-10'))
    a,b=wallet(),wallet('CNY')
    for account,currency,rate,amount in [(a,'RUB','1','1000'),(b,'CNY','13','200')]:
        cash.create_entry(account,body(str(account)+'-opening',kind='opening',category='opening',currency=currency,exchange_rate=rate,amount=amount,status='actual',planned_date='2026-09-01',actual_date='2026-09-01'))
    result=cash.report('2026-09-01','2026-09-20')['accounts']
    assert result[0]['currency']=='RUB' and result[0]['actual_closing_minor']==16250
    assert result[1]['actual_closing_minor']==100000 and result[2]['actual_closing_minor']==20000
    legacy=cashflow.cash_report('2026-09-01','2026-09-20')
    assert len(legacy['items'])==1 and len(legacy['events'])==1
    cashflow.create_entry(body('legacy-anchor',kind='opening',category='opening',status='actual',planned_date='2026-09-01',actual_date='2026-09-01'))

def test_date_anchor_excludes_historic_cash_but_keeps_overdue_plan():
    a=wallet()
    cash.create_entry(a,body('historical-1',status='actual',actual_date='2026-08-01',amount='99999'))
    cash.create_entry(a,body('opening-1',kind='opening',category='opening',status='actual',actual_date='2026-09-01',planned_date='2026-09-01',amount='500'))
    plan=cash.create_entry(a,body('overdue-1',direction='outflow',amount='20',planned_date='2026-08-15'))
    report=cash.report('2026-09-10','2026-09-20')['accounts'][1]
    assert report['actual_closing_minor']==50000 and report['forecast_closing_minor']==48000 and report['overdue_minor']==-2000
    cashflow.change_status(plan['id'],'actual','2026-09-11')
    assert cash.report('2026-09-10','2026-09-20')['accounts'][1]['actual_closing_minor']==48000
    assert cash.report('2026-08-01','2026-09-20')['accounts'][1]['period_before_opening']

def transfer(a,b,**changes):
    return dict(request_key='transfer-key-1',from_account_id=a,to_account_id=b,from_amount='100',to_amount='100',from_rate='1',to_rate='1',status='planned',planned_date='2026-09-10',actual_date=None,reference='')|changes

def test_internal_transfer_atomic_settlement_cancel_and_retry():
    a,b=wallet(),wallet(); data=transfer(a,b)
    pair=cash.transfer(data,'owner@example.test')
    assert cash.transfer(data)==pair and count()==2
    cashflow.change_status(pair['from_entry_id'],'actual','2026-09-12')
    result=cash.report('2026-09-01','2026-09-20')
    assert [r['actual_closing_minor'] for r in result['accounts'][1:]]==[-10000,10000]
    assert result['external_inflow_rub_minor']==result['external_outflow_rub_minor']==0
    cashflow.change_status(pair['to_entry_id'],'cancelled')
    assert all(r['status']=='cancelled' for a in cash.report('2026-09-01','2026-09-20')['accounts'] for r in a['items'])
    with pytest.raises(ValueError):cashflow.change_status(pair['from_entry_id'],'actual','2026-09-12')
    with pytest.raises(ValueError):cash.transfer(data|{'from_amount':'99'})

def test_transfer_bad_second_leg_rolls_back_and_fx_not_profit():
    a,b=wallet(),wallet('CNY')
    with pytest.raises(ValueError):cash.transfer(transfer(a,b,to_amount='1.001',to_rate='13'))
    assert count()==0
    cash.transfer(transfer(a,b,to_amount='7',to_rate='13',status='actual',actual_date='2026-09-10'))
    result=cash.report('2026-09-01','2026-09-20')
    assert result['transfer_valuation_delta_rub_minor']==-900 and result['accrual_profit_unchanged']

def test_preview_readonly_confirm_replay_and_external_identity():
    a=wallet(); text=csv('bank-1;2026-09-12;inflow;125.50;RUB;1;marketplace_payout;WB;')
    preview=statement.preview(a,text); assert count()==0
    result=statement.confirm(a,text,preview['fingerprint'],'import-key-1',True)
    assert result['created']==1 and count()==1
    assert statement.confirm(a,text,preview['fingerprint'],'import-key-1',True)==result
    repeated=statement.preview(a,text); assert repeated['skipped']==1
    statement.confirm(a,text,repeated['fingerprint'],'import-key-2',True); assert count()==1
    with pytest.raises(ValueError):statement.preview(a,text.replace('125.50','126.50'))
    with pytest.raises(ValueError):statement.confirm(a,text,preview['fingerprint'],'import-key-1',False)

def test_explicit_plan_match_no_duplicate_and_stale_preview_atomic():
    a=wallet(); plan=cash.create_entry(a,body('planned-bank-1',direction='outflow',amount='50'))
    text=csv(f'bank-2;2026-09-12;outflow;50;RUB;1;other;Paid;{plan["id"]}')
    preview=statement.preview(a,text)
    result=statement.confirm(a,text,preview['fingerprint'],'match-key-1',True)
    assert result['settled']==1 and count()==1
    assert cash.report('2026-09-01','2026-09-20')['accounts'][1]['forecast_closing_minor']==-5000
    assert statement.confirm(a,text,preview['fingerprint'],'match-key-1',True)==result
    plan2=cash.create_entry(a,body('planned-bank-2',amount='99'))
    text2=csv('fresh-1;2026-09-12;inflow;10;RUB;1;other;;',f'fresh-2;2026-09-12;inflow;99;RUB;1;other;;{plan2["id"]}')
    preview2=statement.preview(a,text2); cashflow.change_status(plan2['id'],'cancelled')
    with pytest.raises(ValueError):statement.confirm(a,text2,preview2['fingerprint'],'stale-key-1',True)
    assert count()==2

def test_concurrent_bank_imports_serialize_without_double_cash():
    a=wallet(); text=csv('bank-1;2026-09-12;inflow;10;RUB;1;other;;')
    preview=statement.preview(a,text)
    def apply(_):return statement.confirm(a,text,preview['fingerprint'],'concurrent-key',True)
    with ThreadPoolExecutor(max_workers=4) as pool:results=list(pool.map(apply,range(4)))
    assert all(r==results[0] for r in results) and count()==1

@pytest.mark.parametrize('text',[
    csv('id;2026-09-12;inflow;NaN;RUB;1;other;;'),
    csv('id;2026-09-12;inflow;1;RUB;0;other;;'),
    csv('id;2026-09-12;inflow;1.001;RUB;1;other;;'),
    csv('id;2026-09-12;inflow;1;RUB;1;other;;','id;2026-09-12;inflow;1;RUB;1;other;;'),
    'date;amount\n2026-09-12;100',
])
def test_malformed_statement_no_financial_writes(text):
    a=wallet()
    with pytest.raises((ValueError,ArithmeticError)):statement.preview(a,text)
    assert count()==0

def test_archival_stale_revision_and_receipts_sql_immutable():
    a,b=wallet(),wallet(); pair=cash.transfer(transfer(a,b))
    cash.archive_account(a,1,False)
    with pytest.raises(ValueError):cash.archive_account(a,1,True)
    with pytest.raises(ValueError):cash.create_entry(a,body())
    assert len(cash.report('2026-09-01','2026-09-20')['accounts'][1]['items'])==1
    with core.connect() as conn:
        for sql in ['DELETE FROM cash_transfers','UPDATE cash_transfers SET actor=\'spoof\'',f'INSERT OR REPLACE INTO cash_transfers SELECT * FROM cash_transfers WHERE id={pair["id"]}']:
            with pytest.raises(sqlite3.IntegrityError):conn.execute(sql)

def test_account_create_idempotence_and_import_category_conflict():
    a=cash.create_account('Bank','RUB',request_key='account-create-1')['id']
    assert cash.create_account('Bank','RUB',request_key='account-create-1')['id']==a
    with pytest.raises(ValueError):cash.create_account('Other','RUB',request_key='account-create-1')
    plan=cash.create_entry(a,body('rent-plan-1',category='rent',reference='Lease #1'))
    bad=csv(f'bank-rent;2026-09-12;inflow;100;RUB;1;tax;Bank reference;{plan["id"]}')
    with pytest.raises(ValueError):statement.preview(a,bad)
    good=bad.replace(';tax;',';rent;');preview=statement.preview(a,good)
    assert preview['rows'][0]['plan_reference']=='Lease #1'
    statement.confirm(a,good,preview['fingerprint'],'rent-confirm-1',True)
    saved=cash.report('2026-09-01','2026-09-20')['accounts'][1]['items'][0]
    assert saved['reference']=='Lease #1' and saved['bank_reference']=='Bank reference' and saved['bank_external_id']=='bank-rent'
    assert cash.create_entry(a,body('rent-plan-1',category='rent',reference='Lease #1'))['id']==plan['id']

def test_encrypted_native_backup_restores_accounts_transfers_and_dedup(monkeypatch,tmp_path):
    import backup_tools as backup
    import sync_lock
    from cryptography.fernet import Fernet
    a,b=wallet(),wallet();cash.transfer(transfer(a,b))
    text=csv('bank-1;2026-09-12;inflow;10;RUB;1;other;;');preview=statement.preview(a,text)
    statement.confirm(a,text,preview['fingerprint'],'backup-import-1',True)
    for module in (backup,sync_lock):monkeypatch.setattr(module,'DB_PATH',core.DB_PATH)
    monkeypatch.setattr(backup,'DATA_DIR',tmp_path)
    monkeypatch.setattr(backup,'BACKUP_DIR',tmp_path)
    monkeypatch.setattr(backup,'SETTINGS_PATH',tmp_path/'settings.json')
    monkeypatch.setattr(backup,'DATA_ENCRYPTION_KEY',Fernet.generate_key().decode())
    expected=cash.report('2026-09-01','2026-09-20')
    payload=backup.create_backup(kind='manual',keep=3).read_bytes()
    assert payload.startswith(backup.BACKUP_ENCRYPTION_PREFIX_V2)
    cash.create_entry(a,body('after-backup-1'))
    backup.restore_backup(payload)
    assert cash.report('2026-09-01','2026-09-20')==expected
    assert statement.preview(a,text)['skipped']==1
    with core.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM cash_statement_imports').fetchone()[0]==1
        with pytest.raises(sqlite3.IntegrityError):conn.execute('INSERT OR REPLACE INTO cash_statement_imports SELECT * FROM cash_statement_imports')

def test_old_unscoped_reader_is_not_valid_rollback_after_wallet_writes():
    cashflow.create_entry(body('legacy-fact',status='actual',actual_date='2026-09-12',amount='5'))
    a=wallet();cash.create_entry(a,body('wallet-fact',status='actual',actual_date='2026-09-12',amount='100'))
    assert cashflow.cash_report('2026-09-01','2026-09-20')['actual_closing_minor']==500
    with core.connect() as conn:
        # This is exactly the pre-wallet legacy interpretation, which is unsafe after writes.
        old_unscoped=conn.execute("SELECT SUM(amount_minor) FROM cash_entries WHERE status='actual'").fetchone()[0]
    assert old_unscoped==10500 and old_unscoped!=500

def test_operation_key_collision_never_adopts_manual_cash_as_bank_or_transfer():
    a,b=wallet(),wallet(); data=transfer(a,b)
    cash.create_entry(a,body(data['request_key']+'_from',direction='outflow'))
    with pytest.raises(ValueError):cash.transfer(data)
    assert count()==1
    text=csv('bank-id;2026-09-12;inflow;100;RUB;1;other;;');raw=statement.parse(text)[0]
    bank_key='statement_'+cash.fingerprint([a,'bank-id'])[:40]
    manual_body=statement.body_for(raw,bank_key)
    cash.create_entry(a,manual_body)
    preview=statement.preview(a,text)
    with pytest.raises(ValueError):statement.confirm(a,text,preview['fingerprint'],'collision-bank-1',True)
    with pytest.raises(ValueError):cashflow.create_entry(manual_body)
    assert count()==2
    with core.connect() as conn:
        assert conn.execute('SELECT COUNT(*) FROM cash_statement_imports').fetchone()[0]==0
