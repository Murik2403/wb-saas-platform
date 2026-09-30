import httpx
import pytest
import shop_membership as membership

@pytest.fixture
def gateway(monkeypatch):
    monkeypatch.setattr(membership.config,'INTERNAL_API_URL','https://gateway.example.test')
    monkeypatch.setattr(membership.config,'INTERNAL_API_SECRET','never-log-secret')
    monkeypatch.setattr(membership.config,'TENANT_SLUG','fixture')
    original=httpx.Client;calls=[]
    def install(response):
        def client(**kwargs):
            assert kwargs==dict(timeout=1,follow_redirects=False,trust_env=False)
            def handler(request):
                calls.append(request)
                return response(request) if callable(response) else httpx.Response(200,json=response)
            return original(**kwargs,transport=httpx.MockTransport(handler))
        monkeypatch.setattr(membership.httpx,'Client',client)
    return install,calls

def test_exact_tenant_secret_account_role_null_and_no_cached_success(gateway):
    install,calls=gateway;install({'role':'owner'})
    assert membership.current_role('123')=='owner'
    assert calls[-1].url=='https://gateway.example.test/internal/tenant-member-role'
    assert calls[-1].headers['x-internal-secret']=='never-log-secret'
    assert calls[-1].content==b'{"slug":"fixture","account":"123"}'
    install({'role':None});assert membership.current_role('123') is None
    assert len(calls)==2

@pytest.mark.parametrize('value',[{'role':'admin'},{'role':[]},{'role':'owner','accounts':[]},[],{}])
def test_unknown_role_shape_fails_closed(gateway,value):
    gateway[0](value)
    with pytest.raises(ValueError):membership.current_role('1')

@pytest.mark.parametrize('status',[301,302,401,403,500])
def test_redirect_error_status_fails_closed_without_follow(gateway,status):
    gateway[0](lambda r:httpx.Response(status,headers={'location':'https://foreign.test'}))
    with pytest.raises(ValueError):membership.current_role('1')
    assert len(gateway[1])==1

def test_bounded_json_and_transport_failure(gateway):
    gateway[0](lambda r:httpx.Response(200,content=b' '*1025))
    with pytest.raises(ValueError):membership.current_role('1')
    def fail(r):raise httpx.ConnectError('secret-bearing remote exception')
    gateway[0](fail)
    with pytest.raises(ValueError,match='недоступна') as exc:membership.current_role('1')
    assert 'secret-bearing' not in str(exc.value)

@pytest.mark.parametrize('account',['0','01','1@example.test','-1','9223372036854775808','1.0',1,None])
def test_account_is_canonical_gateway_integer_id(account):
    with pytest.raises(ValueError):membership.account_id(account)

@pytest.mark.parametrize('origin',['http://gateway.test','https://user:secret@gateway.test','https://gateway.test/path','https://gateway.test?query','https://gateway.test#fragment'])
def test_fixed_https_origin_validation(gateway,monkeypatch,origin):
    gateway[0]({'role':'owner'});monkeypatch.setattr(membership.config,'INTERNAL_API_URL',origin)
    with pytest.raises(ValueError):membership.current_role('1')
    assert not gateway[1]
