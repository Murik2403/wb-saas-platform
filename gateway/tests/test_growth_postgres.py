"""Optional real-Postgres proof for portable growth tables and upserts."""
from datetime import datetime, timedelta, timezone
from unittest import mock

import config
from logic import accounts, billing, db as control_db, growth


def test_postgres_growth_upgrade_events_summary(postgres_dsn):
    with mock.patch.object(config, "DB_BACKEND", "postgres"), mock.patch.object(config, "DATABASE_URL", postgres_dsn):
        with control_db.connect() as conn:
            control_db.init_db(conn)
            control_db.init_db(conn)
            account_id = accounts.create_account(conn, "growth-pg@example.com", "password1234")
            at = datetime.now(timezone.utc) - timedelta(days=40)
            with mock.patch.object(growth, "_now", return_value=at):
                growth.capture_registration_source(conn, account_id, {"utm_source": "yandex"}, consent=True, touch_at=at)
            growth.record_trial_started(conn, account_id, occurred_at=at)
            assert growth.record_activation(conn, account_id, "financial", occurred_at=at + timedelta(days=1))
            assert not growth.record_activation(conn, account_id, "financial", occurred_at=at + timedelta(days=1))
            with mock.patch.object(billing, "_now", return_value=at + timedelta(days=15)):
                billing.record_payment_attempt(conn, account_id, "growth-pg-payment", "manual", 2990)
                billing.apply_successful_payment(conn, account_id, "growth-pg-payment")
            summary = growth.growth_summary(conn)
            assert summary["a7"]["converted"] == 1
            assert summary["p30"]["converted"] == 1
            assert summary["cac"] is None
            accounts.delete_account(conn, account_id)
            assert conn.execute("SELECT account_id FROM growth_events WHERE account_id=?", (account_id,)).fetchone() is None
