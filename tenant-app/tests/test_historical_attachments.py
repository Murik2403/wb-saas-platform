import csv as csvlib
import io
import json
from datetime import date
import pytest
from .test_finished_history_import import database, lot, doc, save, preview, csv
from db import core
from db.financial_source import fingerprint
from db.financial_fifo_reconciliation import read_financial_fifo_reconciliation
from db.historical_cogs import project_historical_cogs
from db.migration_import import preview_import, confirm_import, FIELDS
from db.historical_attachments import template


def source():
    core.ensure_financial_schema()
    with core.connect() as c:
        for rrd,event,qty,day,amount in [(11,'Продажа',10,'2026-08-02',200),(12,'Возврат',5,'2026-08-03',-100)]:
            c.execute('INSERT INTO financial_report(rrd_id,nm_id,quantity,operation_date,sale_date,operation_name,doc_type_name,retail_amount,ppvz_for_pay,raw_json) VALUES(?,100,?,?,?,?,?,?,?,?)', (rrd,qty,day,day,event,event,amount,amount,json.dumps({'srid':'WB-order','chrt_id':10})))
        return [dict(r) for r in c.execute('SELECT * FROM financial_report ORDER BY rrd_id')]


def attachments(facts):
    return [dict(doc('sale',11,'2026-08-02','sale',10,retail_amount='200',ppvz_for_pay='200'),source_srid='WB-order',financial_fingerprint=fingerprint(facts[0])),
            dict(doc('return',12,'2026-08-03','return',5,'sale',retail_amount='-100',ppvz_for_pay='-100'),source_srid='WB-order',financial_fingerprint=fingerprint(facts[1]))]


def test_attach_existing_sale_return_without_source_or_physical_changes_and_replay():
    save('finished_lots',[lot()],'opening');facts=source();rows=attachments(facts)
    assert not preview('historical_documents',[{k:v for k,v in r.items() if k not in ('source_srid','financial_fingerprint')} for r in rows])['can_confirm']
    p=preview('historical_attachments',rows);assert p['can_confirm'],p
    args=('historical_attachments',csv('historical_attachments',rows),{f:f for f in FIELDS['historical_attachments']},'attach',p['preview_fingerprint'],'1')
    assert confirm_import(*args)['imported_count']==2
    assert confirm_import(*args)['replayed']
    with core.connect() as c:
        assert [dict(r) for r in c.execute('SELECT * FROM financial_report ORDER BY rrd_id')]==facts
        assert c.execute('SELECT SUM(ready_units) FROM finished_goods_cost_layers').fetchone()[0]==5
        assert c.execute('SELECT COUNT(*) FROM inventory_movements').fetchone()[0]==0
    rec=read_financial_fifo_reconciliation('2026-08-01','2026-08-31')
    assert rec['eligible_fifo_cogs_rub'].tolist()==[100,-50]
    _,_,total=project_historical_cogs(rec)
    assert total['manual_source_cogs_rub']==50 and not total['cogs_complete']


@pytest.mark.parametrize('change',[{'source_srid':'different'},{'quantity':9},{'chrt_id':0},{'retail_amount':'201'},{'financial_fingerprint':'0'*64}])
def test_mismatch_rejected(change):
    save('finished_lots',[lot()],'opening');rows=attachments(source());rows[0].update(change)
    assert not preview('historical_attachments',rows)['can_confirm']


def test_correction_after_preview_fails_and_post_attachment_downgrades():
    save('finished_lots',[lot()],'opening');rows=attachments(source());p=preview('historical_attachments',rows)
    with core.connect() as c:c.execute('UPDATE financial_report SET penalty=1 WHERE rrd_id=11')
    with pytest.raises(ValueError):confirm_import('historical_attachments',csv('historical_attachments',rows),{f:f for f in FIELDS['historical_attachments']},'attach',p['preview_fingerprint'],'1')
    with core.connect() as c:c.execute('UPDATE financial_report SET penalty=0 WHERE rrd_id=11')
    save('historical_attachments',rows,'attached')
    with core.connect() as c:c.execute('UPDATE financial_report SET penalty=1 WHERE rrd_id=11')
    assert read_financial_fifo_reconciliation('2026-08-01','2026-08-31').iloc[0]['status']=='insufficient'


def test_template_prefills_only_source_identity_and_preserves_unknowns():
    source()
    rows=list(csvlib.DictReader(io.StringIO(template(date(2026,8,1),date(2026,8,31))),delimiter=';'))
    assert rows[0]['source_srid']=='WB-order' and rows[1]['retail_amount']=='-100.0'
    assert all(r[k]=='' for r in rows for k in ('source_id','document_proof','lot_source_id','allocation_units','original_sale_document'))
    assert not preview_import('historical_attachments',template(date(2026,8,1),date(2026,8,31)),{f:f for f in FIELDS['historical_attachments']})['can_confirm']


def test_attachment_preserves_actual_return_money_sign_without_repricing_source():
    save('finished_lots',[lot()],'opening');source()
    with core.connect() as c:
        c.execute('UPDATE financial_report SET retail_amount=100,ppvz_for_pay=100 WHERE rrd_id=12')
        facts=[dict(r) for r in c.execute('SELECT * FROM financial_report ORDER BY rrd_id')]
    rows=attachments(facts);rows[1].update(retail_amount='100',ppvz_for_pay='100')
    save('historical_attachments',rows,'attached')
    rec=read_financial_fifo_reconciliation('2026-08-01','2026-08-31')
    assert rec['eligible_fifo_cogs_rub'].tolist()==[100,-50]
    with core.connect() as c:assert c.execute('SELECT retail_amount FROM financial_report WHERE rrd_id=12').fetchone()[0]==100


def test_prior_rrd_cannot_attach_again_with_different_document_and_unused_lot():
    save('finished_lots',[lot(10,0,'10','0'),dict(lot(10,0,'10','0'),source_id='L2')],'opening')
    rows=attachments(source())[:1];save('historical_attachments',rows,'first')
    changed=dict(rows[0],source_id='A2',document_id='different-document',lot_source_id='L2')
    p=preview('historical_attachments',[changed]);assert not p['can_confirm']
    assert 'уже существует' in p['errors'][0]['message']


def test_unknown_variant_missing_srid_and_wrong_return_link_never_confirm():
    save('finished_lots',[lot()],'opening');source()
    for raw in ({'srid':'WB-order'}, {'chrt_id':10}, {'srid':'other-order','chrt_id':10}):
        with core.connect() as c:
            c.execute('UPDATE financial_report SET raw_json=? WHERE rrd_id=12',(json.dumps(raw),))
            facts=[dict(r) for r in c.execute('SELECT * FROM financial_report ORDER BY rrd_id')]
        rows=attachments(facts)
        if 'srid' in raw:rows[1]['source_srid']=raw['srid']
        assert not preview('historical_attachments',rows)['can_confirm']


def test_template_formula_text_protection_and_partial_history_rejection():
    save('finished_lots',[lot()],'opening');facts=source()
    assert not preview('historical_attachments',attachments(facts)[:1])['can_confirm']
    with core.connect() as c:c.execute('UPDATE financial_report SET raw_json=? WHERE rrd_id=11',(json.dumps({'srid':'=1+1','chrt_id':10}),))
    rows=list(csvlib.DictReader(io.StringIO(template(date(2026,8,1),date(2026,8,31))),delimiter=';'))
    assert rows[0]['source_srid']=="'=1+1"


@pytest.mark.parametrize('quantity', [1.5, -1.5, 0, None, 'invalid'])
def test_invalid_source_quantity_is_never_truncated_or_attached(quantity):
    save('finished_lots',[lot(1,0,'10','0')],'opening');source()
    with core.connect() as c:
        c.execute('UPDATE financial_report SET quantity=? WHERE rrd_id=11',(quantity,))
        fact=dict(c.execute('SELECT * FROM financial_report WHERE rrd_id=11').fetchone())
    row=dict(attachments([fact,fact])[0], quantity=1, allocation_units=1)
    assert not preview('historical_attachments',[row])['can_confirm']
    exported=list(csvlib.DictReader(io.StringIO(template(date(2026,8,1),date(2026,8,31))),delimiter=';'))
    assert exported[0]['quantity']==''
    with core.connect() as c:
        assert c.execute('SELECT COUNT(*) FROM migration_historical_documents').fetchone()[0]==0


@pytest.mark.parametrize('value,expected', [(1.0,1),(-5,5),(True,None),('NaN',None),('Infinity',None),(10**9+1,None)])
def test_source_quantity_preserves_whole_units_and_rejects_nonfinite(value,expected):
    from db.historical_attachments import source_quantity
    assert source_quantity(value)==expected
