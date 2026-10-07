from fastapi.testclient import TestClient
import app

def test_v1_auth_fails_closed_without_secret_and_health_remains_local_read(monkeypatch):
    monkeypatch.setattr(app,'PROVISIONER_BEARER_KEY','')
    client=TestClient(app.app)
    assert client.get('/healthz').status_code==200
    assert client.get('/v1/tenants/demo/health').status_code==503

def test_wrong_or_tenant_secret_cannot_reach_control_handler(monkeypatch):
    monkeypatch.setattr(app,'PROVISIONER_BEARER_KEY','synthetic-purpose-key')
    calls=[];monkeypatch.setattr(app,'_client',lambda:calls.append(1))
    client=TestClient(app.app)
    for token in ('','Bearer wrong','Bearer tenant-internal-key','synthetic-purpose-key'):
        assert client.post('/v1/tenants/demo/stop',headers={'Authorization':token}).status_code==403
    assert calls==[]

def test_valid_purpose_key_reaches_named_handler_only(monkeypatch):
    monkeypatch.setattr(app,'PROVISIONER_BEARER_KEY','synthetic-purpose-key')
    client=TestClient(app.app)
    assert client.get('/v1/not-an-allowed-operation',headers={'Authorization':'Bearer synthetic-purpose-key'}).status_code==404
