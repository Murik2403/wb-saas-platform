"""Real local PostgreSQL transactions; no provider requests or production data."""
import threading
import uuid

import pytest
import config
from logic import accounts, billing, db, account_lifecycle, refund_intents


@pytest.mark.parametrize("amounts", [(200000, 150000), (100000, 100000)])
def test_postgres_refund_claims_serialize_and_preserve_reservations(postgres_dsn, monkeypatch, amounts):
    monkeypatch.setattr(config, "DB_BACKEND", "postgres")
    monkeypatch.setattr(config, "DATABASE_URL", postgres_dsn)
    suffix = uuid.uuid4().hex
    with db.connect() as conn:
        db.init_db(conn)
        db.init_db(conn)
        owner = accounts.create_account(conn, f"refund-{suffix}@example.test", "synthetic-password")
        payment_id = f"payment-{suffix}"
        billing.record_payment_attempt(conn, owner, payment_id, "manual", 3000)
        billing.apply_successful_payment(conn, owner, payment_id, period_days=30)
    barrier = threading.Barrier(2)
    results, failures = [], []

    def claim(amount):
        try:
            barrier.wait(timeout=10)
            with db.connect() as conn:
                account_lifecycle.lock_account(conn, owner)
                payment = conn.execute("SELECT * FROM payments WHERE yookassa_payment_id=?", (payment_id,)).fetchone()
                result = refund_intents.claim(conn, owner, payment, amount, billing._refund_cents)
            results.append(result)
        except ValueError:
            results.append("reserved")
        except BaseException as exc:
            failures.append(exc)

    threads = [threading.Thread(target=claim, args=(amount,)) for amount in amounts]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=20)
    assert not any(thread.is_alive() for thread in threads) and not failures
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM refund_intents WHERE account_id=?", (owner,)).fetchall()
        assert len(rows) == 1
        if amounts[0] == amounts[1]:
            assert {result[0] for result in results} == {rows[0]["intent_id"]}
            assert rows[0]["attempt_count"] == 2
            refund_intents.mark_rejected(conn, rows[0]["intent_id"], "synthetic first-call 401")
            assert refund_intents.get(conn, rows[0]["intent_id"])["state"] == "unknown"
        else:
            assert "reserved" in results and rows[0]["amount_cents"] in amounts
        assert rows[0]["provider_description"] == f"MARKETSHELPER refund {rows[0]['intent_id']}"
