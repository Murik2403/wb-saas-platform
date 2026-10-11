from unittest.mock import Mock
import pytest
import app

def request():
    return app.TenantRequest(internal_api_secret='scoped-secret',billing_url='https://example.test/billing',logout_url='https://example.test/logout',internal_api_url='https://example.test')

def test_volume_initializer_is_scoped_and_hardened():
    client=Mock()
    client.containers.run.return_value.wait.return_value={'StatusCode':0}
    app._prepare_tenant_volume(client,'shop-a')
    kwargs=client.containers.run.call_args.kwargs
    assert kwargs['volumes']=={'wb-tenant-data-shop-a':{'bind':'/data','mode':'rw'}}
    assert kwargs['user']=='0:0'
    assert kwargs['network_disabled'] and kwargs['read_only']
    assert kwargs['cap_drop']==['ALL']
    assert set(kwargs['cap_add'])=={'CHOWN','FOWNER'}
    assert 'chown 10001:10001 /data' in kwargs['command'][2]
    assert '-R' not in kwargs['command'][2]
    client.containers.run.return_value.remove.assert_called_once_with(force=True)

def test_initialization_failure_aborts_before_tenant_launch(monkeypatch):
    client=Mock()
    client.containers.run.return_value.wait.return_value={'StatusCode':1}
    monkeypatch.setattr(app,'_client',lambda:client)
    monkeypatch.setattr(app,'_prepare_backup_directory',lambda *a:None)
    monkeypatch.setattr(app,'_volume',lambda *a:None)
    with pytest.raises(RuntimeError):
        app.create_tenant('shop-a',request())
    assert client.containers.run.call_count==1
    client.networks.create.assert_not_called()

def test_volume_initialized_before_start(monkeypatch):
    events=[]
    client=Mock()
    monkeypatch.setattr(app,'_client',lambda:client)
    monkeypatch.setattr(app,'_prepare_backup_directory',lambda *a:None)
    monkeypatch.setattr(app,'_volume',lambda *a:events.append('volume'))
    monkeypatch.setattr(app,'_prepare_tenant_volume',lambda *a:events.append('permissions'))
    monkeypatch.setattr(app,'_network',lambda *a:events.append('network'))
    monkeypatch.setattr(app,'_attach_traefik',lambda *a:None)
    client.containers.run.side_effect=lambda **kw:events.append('start')
    app.create_tenant('shop-a',request())
    assert events==['volume','permissions','network','start']
