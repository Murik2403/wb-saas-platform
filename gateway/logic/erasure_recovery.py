"""Offline/startup resurrection guard; never destroys tenant volumes or archives."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from logic import db, erasure_ledger


def check(*, database=None):
    required = os.environ.get("WB_SAAS_ERASURE_LEDGER_REQUIRED", "0") == "1"
    path = erasure_ledger.configured_path()
    if path is None:
        if required:
            raise erasure_ledger.LedgerUnavailable("Required erasure ledger is not configured")
        return {"enabled": False, "blocked_accounts": 0}
    erasure_ledger.validate_configured()
    if database is not None:
        database = Path(database)
        if db.config.DB_BACKEND != "sqlite" or not database.is_absolute() or database.is_symlink() or not database.is_file():
            raise erasure_ledger.LedgerUnavailable("Restored database must already exist")
    elif db.config.DB_BACKEND == "sqlite" and not db.DEFAULT_DB_PATH.is_file():
        raise erasure_ledger.LedgerUnavailable("Control-plane database must already exist")
    with db.connect(database or db.DEFAULT_DB_PATH) as conn:
        db.init_db(conn)
        seeded = erasure_ledger.seed_existing_deletions(conn)
        blocked = erasure_ledger.reconcile(conn)
    return {"enabled": True, "seeded_accounts": seeded, "blocked_accounts": blocked}


def resume_deletion(account_id, *, database=None, purge=None):
    """Explicit operator retry of previously requested deletion, never a new request."""
    from logic import accounts, account_lifecycle
    if erasure_ledger.configured_path() is None:
        raise erasure_ledger.LedgerUnavailable("Deletion retry requires the independent ledger")
    check(database=database)
    with db.connect(database or db.DEFAULT_DB_PATH) as conn:
        account = conn.execute("SELECT * FROM accounts WHERE id=?", (account_id,)).fetchone()
        if account is None or not dict(account).get("deletion_requested_at"):
            raise account_lifecycle.LifecycleConflict("No prior deletion request")
        if dict(account).get("deleted_at"):
            return {"already_deleted": True}
        if account_lifecycle.unresolved(conn, account_id):
            raise account_lifecycle.LifecycleConflict("Payment reconciliation required")
        tenant = accounts.get_tenant_for_account(conn, account_id)
    if tenant is not None:
        if purge is None:
            import provisioning
            purge = provisioning.remove_tenant
        purge(tenant["slug"], purge_volume=True)
    with db.connect(database or db.DEFAULT_DB_PATH) as conn:
        accounts.delete_account(conn, account_id)
    return {"operational_identity_deleted": True, "archives_deleted": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--initialize", type=Path)
    mode.add_argument("--resume-deletion", type=int)
    parser.add_argument("--database", type=Path)
    args = parser.parse_args(argv)
    try:
        if args.initialize:
            if args.database:
                parser.error("--database requires --check")
            erasure_ledger.initialize(args.initialize)
            result = {"initialized": True}
        elif args.resume_deletion is not None:
            result = resume_deletion(args.resume_deletion, database=args.database)
        else:
            result = check(database=args.database)
    except erasure_ledger.LedgerUnavailable:
        print(json.dumps({"status": "blocked", "reason": "erasure_ledger_unavailable"}))
        return 1
    except Exception:
        print(json.dumps({"status": "blocked", "reason": "recovery_operation_failed"}))
        return 1
    print(json.dumps({"status": "ok", **result}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
