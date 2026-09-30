"""One bounded opt-in job per tick; no external report delivery."""
from datetime import timedelta
from time import monotonic
from db import shop_schedules as schedules,shop_request_bridge as bridge
from db.core import connect
from shop_membership import current_role
from shop_workspace_service import prepare

def membership(job):
    role=current_role(job['actor'])
    if role not in {'owner','manager'}:raise PermissionError('membership_denied')
    return monotonic()

def run_one(*,now=None):
    job=schedules.claim(now)
    if not job:return False
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
