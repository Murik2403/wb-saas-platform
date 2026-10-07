import time
from concurrent.futures import ThreadPoolExecutor
from logic import login_throttle as throttle
from .base import GatewayDbTestCase
class LoginAccountThrottleTests(GatewayDbTestCase):
 def setUp(self):super().setUp();self.init_db()
 def test_normalized_account_bucket_persists_across_connections_and_hides_email(self):
  for _ in range(5):
   with self.connect() as c:throttle.record_failure(c,' Seller@Example.test ','dummy-private-key',now=1000)
  with self.connect() as c:
   self.assertEqual(throttle.remaining(c,'seller@example.test','dummy-private-key',now=1001),299)
   row=c.execute('SELECT account_key FROM login_account_throttle').fetchone()
   self.assertNotIn('seller',row['account_key']);self.assertNotIn('dummy-private-key',row['account_key'])
   self.assertEqual(throttle.remaining(c,'other@example.test','dummy-private-key',now=1001),0)
 def test_locked_failures_do_not_extend_cooldown_and_expiry_resets(self):
  with self.connect() as c:
   for _ in range(5):throttle.record_failure(c,'unknown@example.test',now=1000)
   throttle.record_failure(c,'unknown@example.test',now=1200)
   self.assertEqual(throttle.remaining(c,'unknown@example.test',now=1299),1)
   self.assertEqual(throttle.remaining(c,'unknown@example.test',now=1300),0)
   throttle.record_failure(c,'unknown@example.test',now=1301)
   self.assertEqual(throttle.remaining(c,'unknown@example.test',now=1301),0)
 def test_success_clears_only_account_failures_and_old_window_expires(self):
  with self.connect() as c:
   for _ in range(5):throttle.record_failure(c,'a@example.test',now=1000)
   for _ in range(5):throttle.record_failure(c,'b@example.test',now=1000)
   throttle.clear(c,'a@example.test')
   self.assertEqual(throttle.remaining(c,'a@example.test',now=1100),0)
   self.assertEqual(throttle.remaining(c,'b@example.test',now=1100),200)
   throttle.record_failure(c,'old@example.test',now=10)
   self.assertEqual(throttle.remaining(c,'old@example.test',now=1000),0)
   self.assertIsNone(c.execute('SELECT 1 FROM login_account_throttle WHERE account_key=?',(throttle.account_key('old@example.test'),)).fetchone())
 def test_concurrent_failures_cannot_lose_counts(self):
  with self.connect() as c:throttle.initialize(c)
  def failure(_):
   with self.connect() as c:throttle.record_failure(c,'concurrent@example.test',now=1000)
  with ThreadPoolExecutor(max_workers=8) as workers:list(workers.map(failure,range(8)))
  with self.connect() as c:
   self.assertEqual(throttle.remaining(c,'concurrent@example.test',now=1001),299)
   self.assertEqual(c.execute('SELECT failures FROM login_account_throttle').fetchone()['failures'],5)
