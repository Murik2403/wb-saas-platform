import asyncio,importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest import mock
import app,billing_routes,admin_routes,yookassa_client
from logic import accounts,billing,account_lifecycle as lifecycle
from .base import GatewayDbTestCase
from .test_billing_routes_webhook import _post_request
from .test_account_routes import _request
spec=importlib.util.spec_from_file_location("lifecycle_billing_cycle",Path(__file__).resolve().parents[2]/"scripts/run_billing_cycle.py")
cycle=importlib.util.module_from_spec(spec);spec.loader.exec_module(cycle)

class BillingDeletionRaceTests(GatewayDbTestCase):
 def setUp(self):
  super().setUp();self.init_db()
  with self.connect() as c:
   self.id=accounts.create_account(c,"owner@example.test","synthetic-password-123",email_verified=True)
   c.execute("UPDATE accounts SET status='active',payment_method_id='synthetic-method',current_period_end='2000-01-01' WHERE id=?",(self.id,))
   self.account=accounts.get_account_by_id(c,self.id)
 def test_checkout_timeout_claim_blocks_blind_repeat(self):
  with mock.patch.object(billing_routes,"control_db",SimpleNamespace(connect=self.connect)),mock.patch.object(billing_routes,"_current_account",return_value=self.account),mock.patch.object(billing_routes.csrf,"verify",return_value=True),mock.patch.object(yookassa_client,"create_payment",side_effect=RuntimeError("synthetic network ambiguity")) as upstream:
   self.assertEqual(billing_routes.billing_checkout(_request('/billing/checkout','POST'),'csrf').status_code,502)
   self.assertEqual(billing_routes.billing_checkout(_request('/billing/checkout','POST'),'csrf').status_code,409)
   self.assertEqual(upstream.call_count,1)
  with self.connect() as c:self.assertEqual(c.execute("SELECT state FROM billing_operation_claims").fetchone()["state"],"unknown")
 def test_stale_recurring_due_row_after_delete_never_calls_provider(self):
  with self.connect() as c:lifecycle.request_deletion(c,self.id)
  with mock.patch.object(cycle,"control_db",SimpleNamespace(connect=self.connect)),mock.patch.object(cycle.billing,"accounts_due_for_recurring_charge",return_value=[self.account]),mock.patch.object(yookassa_client,"create_payment",side_effect=AssertionError('must not charge deleted account')):
   self.assertEqual(cycle.charge_due_accounts(),0)
 def test_recurring_unknown_next_run_does_not_charge_again(self):
  with mock.patch.object(cycle,"control_db",SimpleNamespace(connect=self.connect)),mock.patch.object(yookassa_client,"create_payment",side_effect=RuntimeError('synthetic ambiguity')) as upstream:
   self.assertEqual(cycle.charge_due_accounts(),1)
   self.assertEqual(cycle.charge_due_accounts(),0)
   self.assertEqual(upstream.call_count,1)
 def test_deletion_during_provider_request_blocks_purge_and_late_success_reactivation(self):
  def provider(**kwargs):
   with self.connect() as c:self.assertEqual(lifecycle.request_deletion(c,self.id),'blocked_payment')
   return {'id':'synthetic-recurring','status':'succeeded','metadata':kwargs['metadata']}
  with mock.patch.object(cycle,"control_db",SimpleNamespace(connect=self.connect)),mock.patch.object(yookassa_client,"create_payment",side_effect=provider):
   self.assertEqual(cycle.charge_due_accounts(),0)
  with self.connect() as c:
   row=accounts.get_account_by_id(c,self.id)
   self.assertEqual(row['status'],'canceled');self.assertEqual(lifecycle.request_deletion(c,self.id),'ready')
   self.assertEqual(c.execute("SELECT status FROM payments").fetchone()['status'],'succeeded')
 def test_delete_with_unresolved_claim_never_removes_tenant(self):
  with self.connect() as c:lifecycle.begin_payment(c,self.id,'unknown-delete')
  with mock.patch.object(app,'control_db',SimpleNamespace(connect=self.connect)),mock.patch.object(app,'_current_account',return_value=self.account),mock.patch.object(app.csrf,'verify',return_value=True),mock.patch('provisioning.remove_tenant') as purge:
   result=app.account_delete(_request('/account/delete','POST'),'synthetic-password-123','УДАЛИТЬ','csrf')
  self.assertEqual(result.status_code,409);purge.assert_not_called()
 def test_verified_webhook_resolves_unknown_without_double_extension(self):
  with self.connect() as c:
   op=lifecycle.begin_payment(c,self.id,'checkout-test',expected_amount_rub='2990.00')
   lifecycle.mark_payment_outcome(c,op,'unknown')
  payment={'id':'verified-new','status':'succeeded','metadata':{'account_id':str(self.id),'mh_operation_id':op},'amount':{'value':'2990.00','currency':'RUB'}}
  with mock.patch.object(billing_routes,'control_db',SimpleNamespace(connect=self.connect)),mock.patch.object(billing_routes.config,'YOOKASSA_WEBHOOK_CIDRS',('198.51.100.0/24',)),mock.patch.object(billing_routes.webhook_global_limiter,'is_allowed',return_value=True),mock.patch.object(billing_routes.webhook_source_limiter,'is_allowed',return_value=True),mock.patch.object(yookassa_client,'get_payment',return_value=payment):
   result=asyncio.run(billing_routes.yookassa_webhook(_post_request({'object':{'id':payment['id']}})))
   self.assertEqual(result.status_code,200)
   with self.connect() as c:first=accounts.get_account_by_id(c,self.id)['current_period_end']
   asyncio.run(billing_routes.yookassa_webhook(_post_request({'object':{'id':payment['id']}})))
  with self.connect() as c:
   self.assertEqual(accounts.get_account_by_id(c,self.id)['current_period_end'],first)
   self.assertEqual(c.execute('SELECT COUNT(*) FROM payments').fetchone()[0],1)
   self.assertFalse(lifecycle.unresolved(c,self.id))
 def test_admin_reconciliation_is_verified_read_only_and_owner_csrf_protected(self):
  with self.connect() as c:
   op=lifecycle.begin_payment(c,self.id,'checkout-test',expected_amount_rub='2990.00');lifecycle.mark_payment_outcome(c,op,'unknown')
  pid='00000000-0000-0000-0000-000000000001'
  payment={'id':pid,'status':'canceled','metadata':{'account_id':str(self.id),'mh_operation_id':op},'amount':{'value':'2990.00','currency':'RUB'}}
  with mock.patch.object(admin_routes,'control_db',SimpleNamespace(connect=self.connect)),mock.patch.object(admin_routes,'_require_admin',return_value=self.account),mock.patch.object(admin_routes.csrf,'verify',return_value=False),mock.patch.object(yookassa_client,'get_payment') as get:
   self.assertEqual(admin_routes.admin_reconcile_payment(_request('/admin','POST'),op,pid,'wrong').status_code,303);get.assert_not_called()
  with mock.patch.object(admin_routes,'control_db',SimpleNamespace(connect=self.connect)),mock.patch.object(admin_routes,'_require_admin',return_value=self.account),mock.patch.object(admin_routes.csrf,'verify',return_value=True),mock.patch.object(yookassa_client,'get_payment',return_value=payment),mock.patch.object(yookassa_client,'create_payment',side_effect=AssertionError('reconciliation must never write to provider')):
   self.assertEqual(admin_routes.admin_reconcile_payment(_request('/admin','POST'),op,pid,'csrf').status_code,303)
  with self.connect() as c:self.assertFalse(lifecycle.unresolved(c,self.id))

 def test_legacy_pending_reconciliation_requires_matching_amount_and_does_not_charge(self):
  pid='00000000-0000-0000-0000-000000000002'
  with self.connect() as c:
   billing.record_payment_attempt(c,self.id,pid,'manual',2990)
   self.assertEqual(lifecycle.request_deletion(c,self.id),'blocked_payment')
  payment={'id':pid,'status':'canceled','metadata':{'account_id':str(self.id)},'amount':{'value':'2990.00','currency':'RUB'}}
  with mock.patch.object(admin_routes,'control_db',SimpleNamespace(connect=self.connect)),mock.patch.object(admin_routes,'_require_admin',return_value=self.account),mock.patch.object(admin_routes.csrf,'verify',return_value=True),mock.patch.object(yookassa_client,'create_payment',side_effect=AssertionError('read-only reconciliation cannot charge')):
   with mock.patch.object(yookassa_client,'get_payment',return_value={**payment,'amount':{'value':'2991.00','currency':'RUB'}}):
    self.assertEqual(admin_routes.admin_reconcile_known_payment(_request('/admin','POST'),pid,'csrf').status_code,409)
   with self.connect() as c:self.assertTrue(lifecycle.unresolved(c,self.id))
   with mock.patch.object(yookassa_client,'get_payment',return_value=payment):
    self.assertEqual(admin_routes.admin_reconcile_known_payment(_request('/admin','POST'),pid,'csrf').status_code,303)
  with self.connect() as c:
   self.assertFalse(lifecycle.unresolved(c,self.id));self.assertEqual(lifecycle.request_deletion(c,self.id),'ready')

 def _webhook_before_http_response(self,mode,returned_status):
  first_period=[]
  def provider(**kwargs):
   payment={'id':'00000000-0000-0000-0000-000000000099','status':'succeeded','metadata':kwargs['metadata'],'amount':{'value':f"{kwargs['amount_rub']:.2f}",'currency':'RUB'}}
   with mock.patch.object(yookassa_client,'get_payment',return_value=payment):
    result=asyncio.run(billing_routes.yookassa_webhook(_post_request({'object':{'id':payment['id']}})))
   self.assertEqual(result.status_code,200)
   with self.connect() as c:first_period.append(accounts.get_account_by_id(c,self.id)['current_period_end'])
   return {**payment,'status':returned_status,'confirmation':{'confirmation_url':'https://example.test/confirm'}}
  patches=[mock.patch.object(billing_routes,'control_db',SimpleNamespace(connect=self.connect)),mock.patch.object(cycle,'control_db',SimpleNamespace(connect=self.connect)),mock.patch.object(billing_routes,'_current_account',return_value=self.account),mock.patch.object(billing_routes.csrf,'verify',return_value=True),mock.patch.object(billing_routes.config,'YOOKASSA_WEBHOOK_CIDRS',('198.51.100.0/24',)),mock.patch.object(billing_routes.webhook_global_limiter,'is_allowed',return_value=True),mock.patch.object(billing_routes.webhook_source_limiter,'is_allowed',return_value=True),mock.patch.object(yookassa_client,'create_payment',side_effect=provider)]
  from contextlib import ExitStack
  with ExitStack() as stack:
   for patch in patches:stack.enter_context(patch)
   if mode=='checkout':self.assertEqual(billing_routes.billing_checkout(_request('/billing/checkout','POST'),'csrf').status_code,303)
   else:self.assertEqual(cycle.charge_due_accounts(),0)
  self.assertEqual(len(first_period),1)
  with self.connect() as c:
   self.assertEqual(accounts.get_account_by_id(c,self.id)['current_period_end'],first_period[0])
   self.assertEqual(c.execute('SELECT status FROM payments').fetchone()['status'],'succeeded')
   self.assertFalse(lifecycle.unresolved(c,self.id))
   self.assertEqual(lifecycle.request_deletion(c,self.id),'ready')
 def test_checkout_succeeded_http_return_after_webhook_cannot_extend_twice(self):
  self._webhook_before_http_response('checkout','succeeded')
 def test_checkout_pending_http_return_after_webhook_cannot_reopen_payment(self):
  self._webhook_before_http_response('checkout','pending')
 def test_recurring_succeeded_http_return_after_webhook_cannot_extend_twice(self):
  self._webhook_before_http_response('recurring','succeeded')
 def test_recurring_pending_http_return_after_webhook_cannot_reopen_payment(self):
  self._webhook_before_http_response('recurring','pending')
 def test_manual_extension_before_cron_account_lock_prevents_new_charge(self):
  original_lock=lifecycle.lock_account;injected=[]
  def lock_with_manual_success(conn,account_id):
   if not injected:
    injected.append(True)
    with self.connect() as other:
     billing.record_payment_attempt(other,self.id,'manual-interleaved','manual',2990)
     billing.apply_successful_payment(other,self.id,'manual-interleaved')
   return original_lock(conn,account_id)
  with mock.patch.object(cycle,'control_db',SimpleNamespace(connect=self.connect)),mock.patch.object(lifecycle,'lock_account',side_effect=lock_with_manual_success),mock.patch.object(yookassa_client,'create_payment',side_effect=AssertionError('period was extended before lock')) as create:
   self.assertEqual(cycle.charge_due_accounts(),0);create.assert_not_called()
  self.assertEqual(len(injected),1)
  with self.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM billing_operation_claims').fetchone()[0],0)
 def test_admin_claim_reconcile_rejects_different_verified_id_from_requested(self):
  with self.connect() as c:
   op=lifecycle.begin_payment(c,self.id,'checkout-id-binding',expected_amount_rub='2990.00');lifecycle.mark_payment_outcome(c,op,'unknown')
  requested='00000000-0000-0000-0000-000000000020'
  verified={'id':'00000000-0000-0000-0000-000000000021','status':'succeeded','metadata':{'account_id':str(self.id),'mh_operation_id':op},'amount':{'value':'2990.00','currency':'RUB'}}
  with mock.patch.object(admin_routes,'control_db',SimpleNamespace(connect=self.connect)),mock.patch.object(admin_routes,'_require_admin',return_value=self.account),mock.patch.object(admin_routes.csrf,'verify',return_value=True),mock.patch.object(yookassa_client,'get_payment',return_value=verified):
   self.assertEqual(admin_routes.admin_reconcile_payment(_request('/admin','POST'),op,requested,'csrf').status_code,409)
  with self.connect() as c:self.assertTrue(lifecycle.unresolved(c,self.id));self.assertEqual(c.execute('SELECT COUNT(*) FROM payments').fetchone()[0],0)
