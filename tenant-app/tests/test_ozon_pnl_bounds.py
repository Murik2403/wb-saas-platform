import csv,io,uuid,json
import pytest
from db import core
from db.ozon_pnl import COLUMNS,preview_import,confirm_import
import db.ozon_pnl as ledger

@pytest.fixture
def db(tmp_path,monkeypatch):
    monkeypatch.setattr(core,'DB_PATH',tmp_path/'bounded.sqlite');core.init_db()

def events(count=1):
    rows=[]
    for i in range(count):
        rows.append(dict(external_event_id='sale-'+str(i),accrued_at='2026-09-03T12:00:00+03:00',movement_at='2026-09-03T12:00:00+03:00',kind='sale',sku='123',posting_ref='P-'+str(i),return_of='',quantity='1',revenue_rub='10.00',commission_rub='-1.00',logistics_rub='0.00',other_rub='0.00',source_total_rub='9.00',currency='RUB',document_ref='Synthetic'))
    out=io.StringIO();w=csv.DictWriter(out,fieldnames=COLUMNS);w.writeheader();w.writerows(rows)
    p=preview_import(out.getvalue(),'main');confirm_import(out.getvalue(),'main',p['fingerprint'],'fixture')

def test_pnl_row_cap_precedes_any_decode(db,monkeypatch):
    events(2);monkeypatch.setattr(ledger,'PNL_MAX_ROWS',1,raising=False)
    monkeypatch.setattr(ledger.json,'loads',lambda *_:pytest.fail('decode must not run over row cap'))
    with pytest.raises(ValueError,match='безопасн'):ledger.pnl('2026-09-01','2026-09-30')

def test_pnl_projection_byte_cap_precedes_large_decode(db,monkeypatch):
    events();payload=json.dumps({'events':[],'padding':'x'*6000})
    with core.connect() as conn:
        conn.execute('INSERT INTO ozon_cost_projections(source_key,payload_json,actor,created_at) VALUES(?,?,?,?)',(str(uuid.uuid4()),payload,'fixture','2026-10-01T00:00:00Z'))
    monkeypatch.setattr(ledger,'PNL_MAX_BYTES',5000,raising=False)
    original=json.loads
    def bounded(value):
        assert len(value.encode())<=5000,'oversized projection decoded'
        return original(value)
    monkeypatch.setattr(ledger.json,'loads',bounded)
    with pytest.raises(ValueError,match='безопасн'):ledger.pnl('2026-09-01','2026-09-30')

