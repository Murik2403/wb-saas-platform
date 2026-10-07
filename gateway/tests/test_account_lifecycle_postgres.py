import os
from urllib.parse import urlparse
import pytest
import config
from logic import db,accounts,billing,account_lifecycle as lifecycle

@pytest.fixture
def lifecycle_pg(request):
    dsn=os.environ.get("MH_LIFECYCLE_TEST_DSN")
    if dsn:
        if urlparse(dsn).path != "/mh_lifecycle_test":
            pytest.fail("Explicit DSN must point to disposable mh_lifecycle_test database")
    else:
        dsn=request.getfixturevalue("postgres_dsn")
    original=(config.DB_BACKEND,config.DATABASE_URL)
    config.DB_BACKEND,config.DATABASE_URL="postgres",dsn
    try:yield
    finally:config.DB_BACKEND,config.DATABASE_URL=original

def test_postgres_lifecycle_migration_retention_and_late_settlement(lifecycle_pg):
    with db.connect() as c:
        db.init_db(c)
        account=accounts.create_account(c,"lifecycle-pg@example.test","synthetic-password-123",email_verified=True)
        accounts.set_account_status(c,account,"active")
        billing.record_payment_attempt(c,account,"lifecycle-pg-payment","manual",2990,status="succeeded")
        # Upgrade an existing schema, not just a fresh CREATE TABLE path.
        c.execute("ALTER TABLE accounts DROP COLUMN deletion_requested_at")
        c.execute("ALTER TABLE accounts DROP COLUMN deleted_at")
    with db.connect() as c:
        db.init_db(c);db.init_db(c)
        assert accounts.get_account_by_id(c,account)["email"]=="lifecycle-pg@example.test"
        op=lifecycle.begin_payment(c,account,"lifecycle-pg-intent",expected_amount_rub="2990.00")
        lifecycle.mark_payment_outcome(c,op,"unknown")
        assert lifecycle.request_deletion(c,account)=="blocked_payment"
        payment={"id":"lifecycle-pg-payment","status":"succeeded","amount":{"value":"2990.00","currency":"RUB"},"metadata":{"account_id":str(account),"mh_operation_id":op}}
        lifecycle.bind_verified_provider(c,account,op,payment)
        billing.apply_successful_payment(c,account,payment["id"],payment_method_id="synthetic-method")
        assert accounts.get_account_by_id(c,account)["status"]=="canceled"
        accounts.delete_account(c,account)
        assert accounts.get_account_by_id(c,account) is None
        assert c.execute("SELECT account_id FROM payments WHERE yookassa_payment_id=?",(payment["id"],)).fetchone()["account_id"]==account
        assert c.execute("SELECT state FROM billing_operation_claims WHERE operation_id=?",(op,)).fetchone()["state"]=="completed"


def test_postgres_cron_waits_for_manual_payment_lock_and_rechecks_due(lifecycle_pg):
    import importlib.util,threading,time
    from pathlib import Path
    from unittest import mock
    from types import SimpleNamespace
    import yookassa_client
    spec=importlib.util.spec_from_file_location("lifecycle_pg_cycle",Path(__file__).resolve().parents[2]/"scripts/run_billing_cycle.py")
    cycle=importlib.util.module_from_spec(spec);spec.loader.exec_module(cycle)
    with db.connect() as c:
        db.init_db(c)
        account=accounts.create_account(c,"pg-lock-check@example.test","synthetic-password-123",email_verified=True)
        c.execute("UPDATE accounts SET status='active',payment_method_id='synthetic-method',current_period_end='2000-01-01' WHERE id=?",(account,))
    locked=threading.Event();commit_writer=threading.Event();stale_read=threading.Event();locking_cron=threading.Event()
    errors=[];due_ids=[];cron_pid=[];results=[]
    original_lock=lifecycle.lock_account;original_due=billing.accounts_due_for_recurring_charge
    def writer():
        try:
            with db.connect() as c:
                original_lock(c,account)
                billing.record_payment_attempt(c,account,"pg-manual-before-lock","manual",2990)
                billing.apply_successful_payment(c,account,"pg-manual-before-lock")
                locked.set()
                assert commit_writer.wait(15),"writer release timeout"
        except BaseException as error:errors.append(error);locked.set()
    def observed_due(c):
        rows=original_due(c);due_ids.extend(int(row['id']) for row in rows);stale_read.set();return rows
    def observed_lock(c,account_id):
        if int(account_id)==account:
            cron_pid.append(c.execute("SELECT pg_backend_pid() AS pid").fetchone()['pid'])
            locking_cron.set()
        return original_lock(c,account_id)
    def cron():
        try:results.append(cycle.charge_due_accounts())
        except BaseException as error:errors.append(error)
    write_thread=threading.Thread(target=writer,daemon=True);write_thread.start()
    assert locked.wait(10) and not errors
    with mock.patch.object(cycle,'control_db',SimpleNamespace(connect=db.connect)),mock.patch.object(billing,'accounts_due_for_recurring_charge',side_effect=observed_due),mock.patch.object(lifecycle,'lock_account',side_effect=observed_lock),mock.patch.object(yookassa_client,'create_payment',side_effect=AssertionError('manual payment already extended period')) as provider:
        cron_thread=threading.Thread(target=cron,daemon=True);cron_thread.start()
        try:
            assert stale_read.wait(10) and account in due_ids
            assert locking_cron.wait(10) and cron_pid
            observed_wait=False;deadline=time.monotonic()+10
            with db.connect() as probe:
                while time.monotonic()<deadline:
                    row=probe.execute("SELECT wait_event_type FROM pg_stat_activity WHERE pid=?",(cron_pid[0],)).fetchone()
                    if row and row['wait_event_type']=='Lock':observed_wait=True;break
                    time.sleep(.02)
            assert observed_wait,"cron must actually wait on the writer's row lock"
            assert cron_thread.is_alive();provider.assert_not_called()
        finally:
            commit_writer.set();write_thread.join(15);cron_thread.join(15)
        assert not errors and not write_thread.is_alive() and not cron_thread.is_alive()
        assert results==[0];provider.assert_not_called()
    with db.connect() as c:
        assert accounts.get_account_by_id(c,account)['current_period_end']>billing._now_iso()
        assert c.execute("SELECT COUNT(*) AS n FROM billing_operation_claims WHERE account_id=?",(account,)).fetchone()['n']==0
    print("PG_LOCK_WAIT_OBSERVED=true PROVIDER_CALLS=0")


def test_postgres_deletion_request_and_charge_claim_are_serialized(lifecycle_pg):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    with db.connect() as c:
        db.init_db(c)
        account=accounts.create_account(c,"pg-deletion-race@example.test","synthetic-password-123",email_verified=True)
        accounts.set_account_status(c,account,'active')
    barrier=threading.Barrier(2)
    def charge():
        barrier.wait(10)
        try:
            with db.connect() as c:lifecycle.begin_payment(c,account,'pg-race-claim',expected_amount_rub='2990.00')
            return 'claimed'
        except lifecycle.LifecycleConflict:return 'blocked'
    def delete():
        barrier.wait(10)
        with db.connect() as c:return lifecycle.request_deletion(c,account)
    with ThreadPoolExecutor(max_workers=2) as workers:
        charge_future=workers.submit(charge);delete_future=workers.submit(delete)
        outcome=(charge_future.result(timeout=15),delete_future.result(timeout=15))
    assert outcome in [('claimed','blocked_payment'),('blocked','ready')]
    with db.connect() as c:
        assert accounts.get_account_by_id(c,account)['deletion_requested_at']
        assert not accounts.account_has_access(accounts.get_account_by_id(c,account))
    print('PG_DELETION_CHARGE_SERIALIZED=true')
