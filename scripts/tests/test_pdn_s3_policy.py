import io
import urllib.error

import pytest
from scripts import pdn_s3_policy as policy


VERSION=b'<VersioningConfiguration xmlns="http://s3.amazonaws.com/doc/2006-03-01/"/>'
NOLOCK=b'<Error><Code>ObjectLockConfigurationNotFoundError</Code></Error>'


class Response(io.BytesIO):
    def __init__(self,status,body):super().__init__(body);self.status=status


class Opener:
    def __init__(self,responses):self.responses=iter(responses);self.calls=[]
    def open(self,request,timeout):
        self.calls.append((request,timeout));status,body=next(self.responses)
        if status>=300:raise urllib.error.HTTPError(request.full_url,status,'synthetic',{},io.BytesIO(body))
        return Response(status,body)


@pytest.fixture
def config(monkeypatch):
    value={'endpoint':'https://s3.twcstorage.ru','region':'ru-1','access_key_id':'synthetic-access','secret_access_key':'synthetic-secret'}
    monkeypatch.setattr(policy,'_config',lambda:value)
    return value


def test_verify_requires_exact_safe_policy_and_only_signed_gets(config,monkeypatch):
    opener=Opener([(200,VERSION),(404,NOLOCK)]);monkeypatch.setattr(policy,'_opener',lambda:opener)
    assert policy.verify()=={'versions_checked':True,'object_lock_checked':True,'unversioned':True,'object_lock_disabled':True}
    assert len(opener.calls)==2
    for request,timeout in opener.calls:
        assert request.get_method()=='GET' and timeout==10
        assert request.full_url.startswith('https://s3.twcstorage.ru/marketshelper-backups?')
        assert 'AWS4-HMAC-SHA256' in request.get_header('Authorization')
        assert 'synthetic-secret' not in str(request.header_items())
        assert request.data is None


@pytest.mark.parametrize('body',[
 b'<VersioningConfiguration><Status>Enabled</Status></VersioningConfiguration>',
 b'<VersioningConfiguration><Status>Suspended</Status></VersioningConfiguration>',
 b'<VersioningConfiguration><Status/></VersioningConfiguration>',
 b'<VersioningConfiguration><Unknown/></VersioningConfiguration>',
 b'<Error><Code>AccessDenied</Code></Error>', b'bad XML',
 b'<!DOCTYPE x [<!ENTITY y "expansion">]><VersioningConfiguration>&y;</VersioningConfiguration>',
])
def test_versioning_uncertain_or_enabled_fails_closed(config,monkeypatch,body):
    opener=Opener([(200,body)]);monkeypatch.setattr(policy,'_opener',lambda:opener)
    with pytest.raises(policy.PolicyUnavailable):policy.verify()
    assert len(opener.calls)==1


@pytest.mark.parametrize('status,body',[
 (200,b'<ObjectLockConfiguration><ObjectLockEnabled>Enabled</ObjectLockEnabled></ObjectLockConfiguration>'),
 (200,b'<ObjectLockConfiguration/>'),(404,b'<Error><Code>NoSuchBucket</Code></Error>'),
 (403,b'<Error><Code>AccessDenied</Code></Error>'),(404,b''),(500,NOLOCK),(301,NOLOCK),
])
def test_object_lock_unknown_never_claims_disabled(config,monkeypatch,status,body):
    opener=Opener([(200,VERSION),(status,body)]);monkeypatch.setattr(policy,'_opener',lambda:opener)
    with pytest.raises(policy.PolicyUnavailable):policy.verify()


def test_transport_error_has_no_provider_detail(config,monkeypatch):
    class Failed:
        def open(self,*a,**kw):raise urllib.error.URLError('sensitive credential text')
    monkeypatch.setattr(policy,'_opener',lambda:Failed())
    with pytest.raises(policy.PolicyUnavailable) as error:policy.verify()
    assert 'sensitive' not in str(error.value)


def test_response_limit(config,monkeypatch):
    opener=Opener([(200,b'x'*65537)]);monkeypatch.setattr(policy,'_opener',lambda:opener)
    with pytest.raises(policy.PolicyUnavailable):policy.verify()


@pytest.mark.parametrize('endpoint',['https://evil.example','http://s3.twcstorage.ru','https://s3.twcstorage.ru/other','https://s3.twcstorage.ru@evil.example','https://s3.twcstorage.ru?query=1','https://s3.twcstorage.ru:8443'])
def test_endpoint_cannot_leak_credential_signature(config,monkeypatch,endpoint):
    config['endpoint']=endpoint
    class Never:
        def open(self,*a,**kw):pytest.fail('Unsafe endpoint contacted')
    monkeypatch.setattr(policy,'_opener',lambda:Never())
    with pytest.raises(policy.PolicyUnavailable):policy.verify()


def test_redirect_handler_refuses_signed_redirect():
    handler=policy.NoRedirect()
    assert handler.redirect_request(None,None,302,'found',{},'https://evil.example') is None


def test_config_missing_does_not_initialize(monkeypatch,tmp_path):
    path=tmp_path/'missing';monkeypatch.setattr(policy,'CONFIG_PATH',path);monkeypatch.setattr(policy,'_is_root',lambda:True)
    with pytest.raises(policy.PolicyUnavailable):policy._config()
    assert not path.exists()


def test_config_nonroot_rejected(monkeypatch):
    monkeypatch.setattr(policy,'_is_root',lambda:False)
    with pytest.raises(policy.PolicyUnavailable):policy._config()


def test_cli_failure_returns_generic_safe_json(monkeypatch,capsys):
    def failed():raise policy.PolicyUnavailable('private-secret-value')
    monkeypatch.setattr(policy,'verify',failed)
    assert policy.main()==1
    captured=capsys.readouterr()
    assert captured.err=='' and 'private-secret' not in captured.out
    assert 's3_policy_verification_failed' in captured.out


def test_cli_success_emits_verified_flags(config,monkeypatch,capsys):
    opener=Opener([(200,VERSION),(404,NOLOCK)])
    monkeypatch.setattr(policy,'_opener',lambda:opener)
    assert policy.main()==0
    assert '"versions_checked": true' in capsys.readouterr().out
