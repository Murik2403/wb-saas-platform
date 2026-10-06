import sys
from pathlib import Path
from fastapi import FastAPI
from fastapi.testclient import TestClient
API_DIR=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(API_DIR));sys.path.insert(0,str(API_DIR.parents[1]/'tenant-app'))
import ozon_accounting_api
from db import core
import db.ozon_pnl as ledger


def test_legacy_pnl_budget_rejection_is_422(monkeypatch,tmp_path):
    monkeypatch.setattr(core,'DB_PATH',tmp_path/'bounded-api.sqlite');core.init_db()
    with core.connect() as conn:
        ledger.ensure_schema(conn)
        conn.execute('INSERT INTO ozon_document_events(source_key,payload_json,actor,created_at) VALUES(?,?,?,?)',('synthetic-budget','{}','fixture','2026-09-01T00:00:00Z'))
    monkeypatch.setattr(ledger,'PNL_MAX_ROWS',0)
    app=FastAPI();app.include_router(ozon_accounting_api.router)
    with TestClient(app) as client:
        response=client.get('/api/v1/ozon-accounting/pnl?start_date=2026-09-01&end_date=2026-09-30')
    assert response.status_code==422
    assert 'безопасн' in response.json()['detail']
