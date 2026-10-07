"""Host scheduler: fresh volume snapshots, encrypted offsite copy and watchdog.

Run with the dedicated crypto venv. Gateway owns account/config access; host
never serializes secrets. All input code comes from this committed scripts dir.
"""
import argparse,fcntl,json,os,re,subprocess,sys
from pathlib import Path
import backup_offsite

SCRIPTS=Path(__file__).resolve().parent

def gateway_exec(source,args=(),env=None):
    helper=(SCRIPTS/'backup_offsite.py').read_text()
    prefix="import types,sys\nm=types.ModuleType('backup_offsite')\nexec("+repr(helper)+",m.__dict__)\nsys.modules['backup_offsite']=m\n"
    # Compatibility with P0: authenticated caller uses the same purpose-bound
    # key as P1. A configured explicit key always wins; no credential is logged.
    prefix+="""import provisioning,config
if not hasattr(provisioning,'provisioner_headers'):
 def provisioner_headers():
  import hmac,hashlib
  key=getattr(config,'PROVISIONER_BEARER_KEY','')
  if not key and config.INTERNAL_API_SECRET:key='v1.'+hmac.new(config.INTERNAL_API_SECRET.encode(),b'provisioner-control:v1',hashlib.sha256).hexdigest()
  return {'Authorization':'Bearer '+key} if key else {}
 provisioning.provisioner_headers=provisioner_headers
"""
    code=prefix+"exec("+repr(source)+",{'__name__':'__main__','__file__':'/app/scripts/backup_tenant_volumes.py'})"
    result=subprocess.run(['docker','exec','-i','wb-saas-gateway-1','python','-c',code,*args],input=None,capture_output=True,text=True,timeout=7200,env=env)
    return result.returncode

def alert_command(recipient):
    if not re.fullmatch(r'[^\s@]{1,128}@[^\s@]{1,128}\.[^\s@]{1,32}',recipient):raise ValueError('invalid_alert_recipient')
    source=(SCRIPTS/'backup_alert.py').read_text()
    code="exec("+repr(source)+",{'__name__':'__main__','__file__':'/app/scripts/backup_alert.py'})"
    return ['docker','exec','-i','wb-saas-gateway-1','python','-c',code,'--to',recipient]

def environment(recipient):
    env=os.environ.copy()
    env.setdefault('WB_SAAS_BACKUP_DIR','/opt/wb-saas/backups')
    env.setdefault('WB_SAAS_BACKUP_REQUIRED_GROUPS','control-plane,demo,mvvorokov,marketing')
    env.setdefault('WB_SAAS_BACKUP_STATUS_FILE','/opt/wb-saas/backup-offsite-v2-status.json')
    env.setdefault('WB_SAAS_OFFSITE_REMOTE','timeweb-s3:marketshelper-backups')
    env.setdefault('WB_SAAS_OFFSITE_PUBLIC_RECIPIENT','/opt/wb-saas/offsite-recipient-public.pem')
    env.setdefault('WB_SAAS_OFFSITE_STAGING_DIR','/opt/wb-saas/offsite-v2-staging')
    env.setdefault('WB_SAAS_OFFSITE_LOG_FILE','/var/log/wb-saas-offsite-v2.log')
    env.setdefault('WB_SAAS_OFFSITE_KEY_ESCROW_CONFIRMED','0')
    env['WB_SAAS_BACKUP_ALERT_COMMAND_JSON']=json.dumps(alert_command(recipient))
    # Include every valid encrypted historical namespace. Freshness remains
    # enforced only for the required live groups. Plaintext is never copied.
    root=Path(env['WB_SAAS_BACKUP_DIR'])
    history=[p.name for p in root.iterdir() if p.is_dir() and not p.is_symlink() and re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',p.name) and any(p.glob('*.tar.gz.enc'))]
    env['WB_SAAS_OFFSITE_ARCHIVE_GROUPS']=','.join(history)
    env.pop('WB_SAAS_OFFSITE_RETENTION_DAYS',None);env.pop('WB_SAAS_OFFSITE_DELETE_CONFIRMED',None)
    return env

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['daily','check'])
    parser.add_argument('--alert-to',default='mvvorokov@gmail.com')
    parser.add_argument('--keep-days',type=int,default=14)
    args=parser.parse_args()
    if not 1<=args.keep_days<=3650:parser.error('retention must be between1 and3650 days')
    if os.geteuid()!=0:raise RuntimeError('host_root_required')
    with open('/var/lock/marketshelper-backup-cron.lock','a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print(json.dumps({'skipped':'another_backup_job_running'}));return 0
        env=environment(args.alert_to)
        if args.mode=='check':return backup_offsite.check_freshness(env)
        result=gateway_exec((SCRIPTS/'backup_tenant_volumes.py').read_text(),['--keep-days',str(args.keep_days)])
        if result:
            print(json.dumps({'local_backup_failed':True,'alert':backup_offsite.alert({'kind':'backup_failure','code':'local_backup_failed'},env)}))
            return result
        return backup_offsite.run(env)

if __name__=='__main__':raise SystemExit(main())
