"""One bounded opt-in job per tick; no external report delivery."""
from datetime import timedelta
from time import monotonic
from db import shop_schedules as schedules,shop_request_bridge as bridge
from db.core import connect
from shop_membership import current_role
from shop_workspace_service import prepare
from reports.pdf_budget import PdfBusy

# Only successfully deferred PDF jobs earn priority, never a sync or an arbitrary
# future/due schedule. The parent worker owns the beacon, not the short-lived child.
_waiting_pdf = {}


def _waiting_key(job):
    from db import core
    return (str(core.DB_PATH.resolve()), job['id'])


def pdf_waiting():
    """Revalidate deferred identities so disable/reconfigure/revoke cannot leave a phantom beacon."""
    from db import core
    path = str(core.DB_PATH.resolve())
    if not any(key[0] == path for key in _waiting_pdf):
        return False  # disabled-by-default tenants need no extra database access
    with connect() as conn:
        for key, expected in list(_waiting_pdf.items()):
            if key[0] != path:
                continue
            row = conn.execute('SELECT * FROM shop_schedules WHERE id=?', (key[1],)).fetchone()
            valid = bool(row and row['enabled'] and row['kind'] == 'pdf'
                         and (row['generation'], row['next_due_utc'], row['attempts']) == expected)
            if valid:
                try:
                    if current_role(row['actor']) not in {'owner', 'manager'}:
                        valid = False
                    else:
                        record = schedules.target(conn, row['shop_id'], row['actor'], True)
                        valid = record['account_identity'] == row['identity']
                except (PermissionError, ValueError):
                    valid = False
            if not valid:
                _waiting_pdf.pop(key, None)
    return any(key[0] == path for key in _waiting_pdf)

def membership(job):
    role=current_role(job['actor'])
    if role not in {'owner','manager'}:raise PermissionError('membership_denied')
    return monotonic()

def run_one(*,now=None):
    job=schedules.claim(now)
    if not job:return False
    _waiting_pdf.pop(_waiting_key(job), None)
    try:
        membership(job)
        with connect() as conn:
            conn.execute('BEGIN')
            record=schedules.check(conn,job,now)
            credential=schedules.credential(record)
            revision=record['revision']
        end=schedules.timevalue(job['next_due_utc']).date()
        start=end-timedelta(days=job['period_days']-1)
        action='sync' if job['kind']=='sync' else 'scheduled_pdf'
        arguments={'days':job['period_days']} if action=='sync' else {
            'schedule_id':job['id'],'generation':job['generation'],'metrics':__import__('json').loads(job['metrics_json']),
            'run_key':job['run_key']}
        request={'schedule_id':job['id'],'generation':job['generation'],'due_utc':job['next_due_utc'],
                 'kind':job['kind'],'start':start.isoformat(),'end':end.isoformat(),'arguments':arguments}
        prepared=prepare(job['shop_id'],action,start.isoformat(),end.isoformat(),_timeout=600 if action=='sync' else 90,
                         actor=job['actor'],role='manager',**arguments)
        if action=='scheduled_pdf' and prepared['response'].get('pdf_busy') is True:
            raise PdfBusy('Shared PDF budget unavailable')
        if (prepared['identity'],prepared['base_revision'])!=(job['identity'],revision):raise ValueError('snapshot_conflict')
        if action=='sync' and not prepared['synced']:raise ValueError('sync_incomplete')
        verified_at=membership(job)
        with connect() as conn:
            conn.execute('BEGIN IMMEDIATE')
            # Result is fresh, not a network request while holding the writer.
            if monotonic()-verified_at>5:raise ValueError('membership_expired')
            record=schedules.check(conn,job,now)
            if schedules.credential(record)!=credential:raise ValueError('credential_changed')
            result=bridge.commit(job['shop_id'],job['identity'],revision,job['run_key'],request,job['actor'],'manager',
                    snapshot=prepared['snapshot'],response=prepared['response'],synced=prepared['synced'],
                    source_errors=prepared['source_errors'],_conn=conn)
            artifact=prepared['response'].get('artifact')
            schedules.finish(conn,job,status='ok',artifact=artifact,revision=result['bridge']['applied_revision'],now=now)
    except PdfBusy:
        try:
            with connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                schedules.defer_pdf_busy(conn,job,now=now)
            if job['kind'] == 'pdf':
                _waiting_pdf[_waiting_key(job)] = (job['generation'], job['next_due_utc'], job['attempts'])
                from reports.pdf_budget import announce_background_waiting
                announce_background_waiting()
        except Exception:
            pass  # obsolete lease/configuration must not affect its replacement
    except Exception as exc:
        # Only bounded machine codes; child/network/token errors never leak.
        code='membership_denied' if isinstance(exc,PermissionError) else (
            str(exc) if str(exc) in {'snapshot_conflict','sync_incomplete','membership_expired','credential_changed'} else 'run_failed')
        try:
            with connect() as conn:
                conn.execute('BEGIN IMMEDIATE')
                schedules.finish(conn,job,status='failed',error_code=code,now=now)
        except Exception:
            # Disabled/reconfigured/lost lease: obsolete job must not change it.
            pass
    return True
