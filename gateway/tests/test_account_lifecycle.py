from concurrent.futures import ThreadPoolExecutor
import threading
from unittest import mock
from logic import accounts,billing,account_lifecycle as lifecycle
from .base import GatewayDbTestCase

class AccountLifecycleTests(GatewayDbTestCase):
 def setUp(self):
  super().setUp();self.init_db()
  with self.connect() as c:
   self.account=accounts.create_account(c,"original@example.test","synthetic-password-123",email_verified=True)
   accounts.set_account_status(c,self.account,"active")
 def test_tombstone_preserves_payment_refund_and_removes_operational_identity(self):
  with self.connect() as c:
   pid=billing.record_payment_attempt(c,self.account,"synthetic-payment","manual",2990,status="succeeded")
   billing.apply_refund(c,self.account,"synthetic-payment",100,"synthetic-refund")
   token=accounts.create_session(c,self.account)
   accounts.create_tenant_instance(c,self.account,"synthetic")
   self.assertEqual(lifecycle.request_deletion(c,self.account),"ready")
   accounts.delete_account(c,self.account)
   self.assertIsNone(accounts.get_account_by_id(c,self.account))
   tomb=accounts.get_account_by_id(c,self.account,include_deleted=True)
   self.assertNotEqual(tomb["email"],"original@example.test")
   self.assertEqual((tomb["password_hash"],tomb["payment_method_id"]),("",""))
   self.assertIsNone(accounts.resolve_session(c,token))
   self.assertEqual(c.execute("SELECT id,amount_rub FROM payments").fetchone()["id"],pid)
   self.assertEqual(c.execute("SELECT payment_id FROM refunds").fetchone()["payment_id"],pid)
   self.assertFalse(accounts.account_has_access(tomb))
   new=accounts.create_account(c,"original@example.test","synthetic-password-123")
   self.assertNotEqual(new,self.account)
   self.assertEqual(billing.list_payments(c,new),[])
 def test_unknown_payment_never_expires_and_blocks_deletion_and_retry(self):
  with self.connect() as c:
   op=lifecycle.begin_payment(c,self.account,"intent",expected_amount_rub="2990.00")
   lifecycle.mark_payment_outcome(c,op,"unknown")
   c.execute("UPDATE billing_operation_claims SET created_at='2000-01-01'")
   self.assertEqual(lifecycle.request_deletion(c,self.account),"blocked_payment")
   with self.assertRaises(lifecycle.LifecycleConflict):lifecycle.begin_payment(c,self.account,"different-intent")
   with self.assertRaises(lifecycle.LifecycleConflict):accounts.delete_account(c,self.account)
   self.assertIsNotNone(accounts.get_account_by_id(c,self.account))
 def test_verified_provider_reconciles_unknown_but_not_wrong_amount_or_metadata(self):
  with self.connect() as c:
   op=lifecycle.begin_payment(c,self.account,"intent",expected_amount_rub="2990.00")
   lifecycle.mark_payment_outcome(c,op,"unknown")
   payment={"id":"synthetic-payment","status":"succeeded","amount":{"value":"2990.00","currency":"RUB"},"metadata":{"account_id":str(self.account),"mh_operation_id":op}}
   bad={**payment,"amount":{"value":"2991.00","currency":"RUB"}}
   with self.assertRaises(lifecycle.LifecycleConflict):lifecycle.bind_verified_provider(c,self.account,op,bad)
   self.assertTrue(lifecycle.unresolved(c,self.account))
   lifecycle.bind_verified_provider(c,self.account,op,payment)
   self.assertFalse(lifecycle.unresolved(c,self.account))
   with self.assertRaises(lifecycle.LifecycleConflict):lifecycle.bind_verified_provider(c,self.account,op,{**payment,"id":"other-payment"})
 def test_late_success_cannot_resurrect_pending_or_finalized_deletion(self):
  with self.connect() as c:
   billing.record_payment_attempt(c,self.account,"late","manual",2990)
   lifecycle.request_deletion(c,self.account)
   billing.apply_successful_payment(c,self.account,"late",payment_method_id="synthetic-method")
   row=accounts.get_account_by_id(c,self.account)
   self.assertEqual((row["status"],row["payment_method_id"]),("canceled",""))
   accounts.delete_account(c,self.account)
   billing.apply_successful_payment(c,self.account,"late",payment_method_id="synthetic-method")
   self.assertIsNone(accounts.get_account_by_id(c,self.account))
   self.assertEqual(c.execute("SELECT status FROM payments WHERE yookassa_payment_id='late'").fetchone()["status"],"succeeded")
   with self.assertRaises(ValueError):billing.admin_extend_period(c,self.account,30)
 def test_idempotent_migration_preserves_existing_rows(self):
  with self.connect() as c:
   before=dict(accounts.get_account_by_id(c,self.account))
   lifecycle.initialize(c);lifecycle.initialize(c)
   self.assertEqual(dict(accounts.get_account_by_id(c,self.account)),before)
 def test_claim_vs_deletion_race_has_exactly_one_winner(self):
  barrier=threading.Barrier(2)
  def charge():
   barrier.wait()
   try:
    with self.connect() as c:lifecycle.begin_payment(c,self.account,"race")
    return "claimed"
   except lifecycle.LifecycleConflict:return "blocked"
  def delete():
   barrier.wait()
   with self.connect() as c:return lifecycle.request_deletion(c,self.account)
  with ThreadPoolExecutor(max_workers=2) as pool:
   a=pool.submit(charge);b=pool.submit(delete);out=(a.result(),b.result())
  self.assertIn(out,[("claimed","blocked_payment"),("blocked","ready")])
 def test_pending_deletion_cannot_be_reactivated(self):
  with self.connect() as c:
   lifecycle.request_deletion(c,self.account)
   with self.assertRaises(ValueError):accounts.set_account_status(c,self.account,"active")
   with self.assertRaises(ValueError):billing.start_trial(c,self.account)
   with self.assertRaises(ValueError):billing.admin_extend_period(c,self.account,30)

 def test_late_refund_keeps_tombstone_canceled_and_financial_links(self):
  with self.connect() as c:
   billing.record_payment_attempt(c,self.account,"refund-late","manual",2990,status="succeeded")
   lifecycle.request_deletion(c,self.account);accounts.delete_account(c,self.account)
   billing.apply_refund(c,self.account,"refund-late",100,"refund-late-id")
   tomb=accounts.get_account_by_id(c,self.account,include_deleted=True)
   self.assertEqual(tomb["status"],"canceled")
   self.assertEqual(tomb["current_period_end"],"")
   self.assertEqual(c.execute("SELECT COUNT(*) FROM refunds").fetchone()[0],1)

 def test_legacy_pending_payment_blocks_final_delete_even_without_new_claim(self):
  with self.connect() as c:
   billing.record_payment_attempt(c,self.account,"legacy-pending","manual",2990)
   self.assertEqual(lifecycle.request_deletion(c,self.account),"blocked_payment")
   with self.assertRaises(lifecycle.LifecycleConflict):accounts.delete_account(c,self.account)
   billing.apply_failed_payment(c,self.account,"legacy-pending",error="canceled")
   self.assertEqual(lifecycle.request_deletion(c,self.account),"ready")

 def test_payment_owner_and_amount_cannot_be_reassigned_on_duplicate_id(self):
  with self.connect() as c:
   other=accounts.create_account(c,'other@example.test','synthetic-password-123')
   billing.record_payment_attempt(c,other,'owned-payment','manual',2990)
   before=dict(accounts.get_account_by_id(c,self.account))
   billing.apply_failed_payment(c,self.account,'owned-payment',error='wrong-owner')
   billing.apply_successful_payment(c,self.account,'owned-payment')
   self.assertEqual(dict(accounts.get_account_by_id(c,self.account)),before)
   self.assertEqual(c.execute("SELECT status FROM payments WHERE yookassa_payment_id='owned-payment'").fetchone()['status'],'pending')
   with self.assertRaises(ValueError):billing.record_payment_attempt(c,self.account,'owned-payment','manual',2990)
   with self.assertRaises(ValueError):billing.record_payment_attempt(c,other,'owned-payment','manual',2991)
 def test_terminal_payment_cannot_be_downgraded_or_grant_access_again(self):
  with self.connect() as c:
   billing.record_payment_attempt(c,self.account,'terminal','manual',2990)
   billing.apply_successful_payment(c,self.account,'terminal')
   before=dict(accounts.get_account_by_id(c,self.account))
   billing.record_payment_attempt(c,self.account,'terminal','manual',2990,status='pending')
   billing.apply_failed_payment(c,self.account,'terminal',error='stale-failure')
   billing.apply_successful_payment(c,self.account,'terminal')
   self.assertEqual(dict(accounts.get_account_by_id(c,self.account)),before)
   self.assertEqual(c.execute("SELECT status FROM payments WHERE yookassa_payment_id='terminal'").fetchone()['status'],'succeeded')
   billing.apply_refund(c,self.account,'terminal',2990,'refund-terminal')
   refunded=dict(accounts.get_account_by_id(c,self.account))
   billing.record_payment_attempt(c,self.account,'terminal','manual',2990,status='pending')
   billing.apply_successful_payment(c,self.account,'terminal')
   self.assertEqual(dict(accounts.get_account_by_id(c,self.account)),refunded)
   self.assertEqual(c.execute("SELECT status FROM payments WHERE yookassa_payment_id='terminal'").fetchone()['status'],'canceled')
