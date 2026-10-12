from datetime import datetime,timedelta,timezone
from unittest import mock
from .base import GatewayDbTestCase
from logic import accounts,billing


class ProportionalRefundTests(GatewayDbTestCase):
    def setUp(self):
        super().setUp();self.init_db()
        self.now=datetime(2026,10,12,12,tzinfo=timezone.utc)
        self.enterContext(mock.patch.object(billing,'_now',return_value=self.now))
        with self.connect() as c:
            self.owner=accounts.create_account(c,'refund@example.test','password1234')
            billing.record_payment_attempt(c,self.owner,'payment','manual',3000)
            billing.apply_successful_payment(c,self.owner,'payment',period_days=30)
        self.end=self.now+timedelta(days=30)

    def account(self):
        with self.connect() as c:return dict(accounts.get_account_by_id(c,self.owner))

    def refund(self,amount,reference='refund',status='succeeded'):
        with self.connect() as c:billing.apply_refund(c,self.owner,'payment',amount,reference,status)

    def test_actual_granted_interval_is_preserved_on_duplicate_payment(self):
        with self.connect() as c:
            original=dict(c.execute("SELECT * FROM payments WHERE yookassa_payment_id='payment'").fetchone())
            billing.apply_successful_payment(c,self.owner,'payment',period_days=90)
            repeat=dict(c.execute("SELECT * FROM payments WHERE yookassa_payment_id='payment'").fetchone())
        self.assertEqual(original['period_start'],self.now.isoformat(timespec='seconds'))
        self.assertEqual(original['period_end'],self.end.isoformat(timespec='seconds'))
        self.assertEqual(repeat['period_end'],original['period_end'])

    def test_half_refund_removes_fifteen_days_and_keeps_access(self):
        self.refund(1500)
        a=self.account();self.assertEqual(a['status'],'active')
        self.assertEqual(datetime.fromisoformat(a['current_period_end']),self.end-timedelta(days=15))

    def test_duplicate_and_pending_notifications_deduct_only_once(self):
        self.refund(300,status='pending');self.assertEqual(datetime.fromisoformat(self.account()['current_period_end']),self.end)
        self.refund(300);first=self.account()
        self.refund(300);self.refund(300,status='pending')
        self.assertEqual(self.account(),first)
        self.assertEqual(datetime.fromisoformat(first['current_period_end']),self.end-timedelta(days=3))

    def test_multiple_refunds_accumulate_without_rounding_drift(self):
        self.refund(0.01,'r1');self.refund(0.01,'r2');self.refund(1499.98,'r3')
        self.assertEqual(datetime.fromisoformat(self.account()['current_period_end']),self.end-timedelta(days=15))

    def test_full_cumulative_refund_expires_without_duplicate_grace_reset(self):
        self.refund(1500,'r1');self.refund(1500,'r2');a=self.account()
        self.assertEqual(a['status'],'past_due')
        self.assertEqual(a['current_period_end'],self.now.isoformat(timespec='seconds'))
        with mock.patch.object(billing,'_now',return_value=self.now+timedelta(days=1)):self.refund(1500,'r2')
        self.assertEqual(self.account(),a)

    def test_refund_rebinding_or_amount_change_is_rejected(self):
        self.refund(300);a=self.account()
        with self.assertRaises(ValueError):self.refund(301)
        with self.connect() as c:
            billing.record_payment_attempt(c,self.owner,'other','manual',3000)
        with self.assertRaises(ValueError):
            with self.connect() as c:billing.apply_refund(c,self.owner,'other',300,'refund')
        self.assertEqual(self.account(),a)

    def test_over_refund_and_nonfinite_money_roll_back(self):
        self.refund(1500,'r1');a=self.account()
        for amount in (1500.01,float('nan'),float('inf'),-1,0,0.001):
            with self.assertRaises(ValueError):self.refund(amount,'r2')
        self.assertEqual(self.account(),a)
        with self.connect() as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM refunds').fetchone()[0],1)

    def test_refund_of_prepaid_old_period_removes_only_its_paid_time(self):
        with self.connect() as c:
            billing.record_payment_attempt(c,self.owner,'later','manual',3000)
            billing.apply_successful_payment(c,self.owner,'later',period_days=30)
        self.refund(1500)
        self.assertEqual(datetime.fromisoformat(self.account()['current_period_end']),self.now+timedelta(days=45))
        self.refund(1500,'r2')
        self.assertEqual(datetime.fromisoformat(self.account()['current_period_end']),self.now+timedelta(days=30))

    def test_refund_of_consumed_old_period_preserves_later_paid_time(self):
        with self.connect() as c:
            billing.record_payment_attempt(c,self.owner,'later','manual',3000)
            billing.apply_successful_payment(c,self.owner,'later',period_days=30)
        a=self.account()
        with mock.patch.object(billing,'_now',return_value=self.now+timedelta(days=31)):
            self.refund(1500)
        self.assertEqual(self.account(),a)

    def test_refund_of_partly_consumed_old_period_is_capped_at_unconsumed_time(self):
        with self.connect() as c:
            billing.record_payment_attempt(c,self.owner,'later','manual',3000)
            billing.apply_successful_payment(c,self.owner,'later',period_days=30)
        with mock.patch.object(billing,'_now',return_value=self.now+timedelta(days=25)):
            self.refund(1500)
        self.assertEqual(datetime.fromisoformat(self.account()['current_period_end']),self.now+timedelta(days=55))

    def test_unknown_legacy_interval_gets_reconciliation_marker(self):
        with self.connect() as c:c.execute("UPDATE payments SET period_start='',period_end='' WHERE yookassa_payment_id='payment'")
        a=self.account();self.refund(1500);self.assertEqual(self.account(),a)
        with self.connect() as c:self.assertEqual(c.execute("SELECT error_message FROM payments WHERE yookassa_payment_id='payment'").fetchone()[0],'refund_access_interval_unknown')

    def test_confirmation_order_not_attempt_id_determines_latest_paid_period(self):
        with self.connect() as c:
            billing.record_payment_attempt(c,self.owner,'attempt-first','manual',3000)
            billing.record_payment_attempt(c,self.owner,'attempt-second','manual',3000)
            billing.apply_successful_payment(c,self.owner,'attempt-second',period_days=30)
            billing.apply_successful_payment(c,self.owner,'attempt-first',period_days=30)
            billing.apply_refund(c,self.owner,'attempt-first',1500,'reverse-order-refund')
        self.assertEqual(datetime.fromisoformat(self.account()['current_period_end']),self.now+timedelta(days=75))
