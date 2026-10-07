from unittest import mock
from types import SimpleNamespace
from fastapi import Request
from fastapi.testclient import TestClient
import app
from logic import accounts,csrf,db,login_throttle
from .base import GatewayDbTestCase

def request(path='/login',ip='198.51.100.1',cookies=''):
 return Request({'type':'http','method':'POST','path':path,'headers':[(b'cookie',cookies.encode())] if cookies else [],'client':(ip,12345),'scheme':'https','server':('marketshelper.ru',443),'query_string':b''})
class LoginAndLogoutSecurityTests(GatewayDbTestCase):
 def setUp(self):
  super().setUp();self.init_db()
  with self.connect() as c:self.account_id=accounts.create_account(c,'owner@example.test','safe-password-123',email_verified=True)
 def test_distributed_ip_failures_lock_account_before_password_hash(self):
  with mock.patch.object(app,'control_db',SimpleNamespace(connect=self.connect)),mock.patch.object(app.auth_rate_limiter,'is_allowed',return_value=True),mock.patch.object(app.csrf,'verify',return_value=True):
   for i in range(5):self.assertEqual(app.login_submit(request(ip=f'198.51.100.{i+1}'),'OWNER@example.test','wrong-password','','csrf').status_code,400)
   with mock.patch.object(app.accounts,'authenticate_account',side_effect=AssertionError('throttled attempts must not hash passwords')):
    result=app.login_submit(request(ip='203.0.113.250'),' owner@example.test ','safe-password-123','','csrf')
   self.assertEqual(result.status_code,429)
   self.assertIn('wb_saas_csrf=',' '.join(result.headers.getlist('set-cookie')))
 def test_invalid_csrf_never_counts_failed_password_and_success_resets(self):
  with mock.patch.object(app,'control_db',SimpleNamespace(connect=self.connect)),mock.patch.object(app.auth_rate_limiter,'is_allowed',return_value=True),mock.patch.object(app.csrf,'verify',return_value=False):
   self.assertEqual(app.login_submit(request(),'owner@example.test','wrong','','csrf').status_code,400)
  with self.connect() as c:
   login_throttle.initialize(c);self.assertEqual(c.execute('SELECT COUNT(*) FROM login_account_throttle').fetchone()[0],0)
   for _ in range(4):login_throttle.record_failure(c,'owner@example.test',app.config.INTERNAL_API_SECRET)
  with mock.patch.object(app,'control_db',SimpleNamespace(connect=self.connect)),mock.patch.object(app.auth_rate_limiter,'is_allowed',return_value=True),mock.patch.object(app.csrf,'verify',return_value=True):
   result=app.login_submit(request(),'owner@example.test','safe-password-123','','csrf')
  self.assertEqual(result.status_code,303)
  with self.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM login_account_throttle').fetchone()[0],0)
 def test_logout_missing_or_wrong_csrf_does_not_revoke_active_session(self):
  with self.connect() as c:session=accounts.create_session(c,self.account_id)
  with mock.patch.object(app,'control_db',SimpleNamespace(connect=self.connect)):
   req=request('/logout',cookies=f'{app.config.SESSION_COOKIE_NAME}={session}; {csrf.CSRF_COOKIE_NAME}=correct')
   for submitted in ('','wrong'):
    self.assertEqual(app.logout(req,submitted).status_code,403)
    with self.connect() as c:self.assertIsNotNone(accounts.resolve_session(c,session))
   self.assertEqual(app.logout(req,'correct').status_code,303)
   with self.connect() as c:self.assertIsNone(accounts.resolve_session(c,session))
 def test_get_confirmation_sets_httponly_csrf_and_does_not_revoke_session(self):
  with self.connect() as c:session=accounts.create_session(c,self.account_id)
  req=request('/logout',cookies=f'{app.config.SESSION_COOKIE_NAME}={session}')
  with mock.patch.object(app,'control_db',SimpleNamespace(connect=self.connect)):
   response=app.logout_confirmation(req)
  self.assertEqual(response.status_code,200);self.assertEqual(response.headers['Cache-Control'],'no-store')
  self.assertIn('name="csrf_token"',response.body.decode());self.assertIn('method="post" action="/logout"',response.body.decode())
  self.assertIn('HttpOnly',' '.join(response.headers.getlist('set-cookie')))
  with self.connect() as c:self.assertIsNotNone(accounts.resolve_session(c,session))
 def test_http_logout_requires_confirmation_token(self):
  with self.connect() as c:session=accounts.create_session(c,self.account_id)
  patchdb=SimpleNamespace(connect=self.connect,init_db=db.init_db)
  with mock.patch.object(app,'control_db',patchdb):
   with TestClient(app.app,base_url='https://marketshelper.ru') as client:
    client.cookies.set(app.config.SESSION_COOKIE_NAME,session)
    self.assertEqual(client.post('/logout',follow_redirects=False).status_code,403)
    get=client.get('/logout');self.assertEqual(get.status_code,200)
    token=client.cookies.get(csrf.CSRF_COOKIE_NAME)
    self.assertTrue(token)
    self.assertEqual(client.post('/logout',data={'csrf_token':token},follow_redirects=False).status_code,303)
  with self.connect() as c:self.assertIsNone(accounts.resolve_session(c,session))
