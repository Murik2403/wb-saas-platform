"""Copy encrypted archives offsite; no deletion without an explicit retention policy.

No credentials are read or logged. rclone owns its configured credential store.
The encryption key must be escrowed separately by the operator, never uploaded here.
"""
import argparse, json, os, re, subprocess, time, uuid
from pathlib import Path

MAX_AGE=26*3600
ERROR_CODES={'invalid_groups','local_backup_stale','encrypted_archive_format_invalid','offsite_unconfigured',
             'offsite_recipient_unconfigured','offsite_public_key_invalid','offsite_source_invalid','offsite_staging_invalid',
             'offsite_staging_overlap','offsite_encryption_timeout','offsite_source_changed','offsite_copy_failed',
             'offsite_verification_failed','remote_retention_unconfirmed','remote_retention_failed'}

def alert(event,env):
    configured=env.get('WB_SAAS_BACKUP_ALERT_COMMAND_JSON')
    if not configured:return 'unconfigured'
    try:
        command=json.loads(configured)
        if not isinstance(command,list) or not command or any(not isinstance(v,str) or not v for v in command):return 'invalid_configuration'
        run=subprocess.run(command,input=json.dumps(event),text=True,capture_output=True,timeout=30)
        return 'accepted' if run.returncode==0 else 'failed'
    except (ValueError,OSError,subprocess.SubprocessError):return 'failed'

def write_status(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.'+path.name+'.'+uuid.uuid4().hex+'.partial')
    descriptor=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    try:
        with os.fdopen(descriptor,'w') as output:
            os.chmod(temporary,0o600);json.dump(value,output);output.flush();os.fsync(output.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)

def check_freshness(env=None):
    """Independent hourly watchdog: a stopped daily copy must still alert."""
    env=os.environ if env is None else env;now=time.time();error=None
    root=Path(env.get('WB_SAAS_BACKUP_DIR','/opt/wb-saas/backups'))
    path=Path(env.get('WB_SAAS_BACKUP_STATUS_FILE','/opt/wb-saas/backup-offsite-status.json'))
    try:
        for group in env.get('WB_SAAS_BACKUP_REQUIRED_GROUPS','control-plane').split(','):
            if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',group):raise ValueError('invalid_groups')
            files=[p for p in (root/group).glob('*.tar.gz.enc') if p.is_file() and not p.is_symlink()]
            if not files or now-max(p.stat().st_mtime for p in files)>MAX_AGE:raise ValueError('local_backup_stale')
        state=json.loads(path.read_text())
        verified=state.get('verified_at')
        if type(verified) not in (int,float) or not 0<=now-verified<=MAX_AGE or state.get('offsite_verified') is not True:raise ValueError('offsite_backup_stale')
    except (ValueError,OSError,AttributeError) as exc:error=str(exc) if isinstance(exc,ValueError) and str(exc) in {'invalid_groups','local_backup_stale','offsite_backup_stale'} else 'offsite_backup_stale'
    status={'checked_at':now,'fresh':error is None,'error':error,'alert':alert({'kind':'backup_freshness_failure','code':error,'at':now},env) if error else 'not_needed'}
    write_status(path.with_name(path.stem+'.health.json'),status);print(json.dumps(status));return int(error is not None)

def run(env=None):
    env=os.environ if env is None else env
    root=Path(env.get('WB_SAAS_BACKUP_DIR','/opt/wb-saas/backups'))
    status_path=Path(env.get('WB_SAAS_BACKUP_STATUS_FILE','/opt/wb-saas/backup-offsite-status.json'))
    now=time.time();status={'attempt_at':now,'local_fresh':False,'offsite_verified':False,'key_escrow_confirmed':env.get('WB_SAAS_OFFSITE_KEY_ESCROW_CONFIRMED')=='1','error':None,'alert':'not_needed'}
    try:
        required=env.get('WB_SAAS_BACKUP_REQUIRED_GROUPS','control-plane').split(',')
        history=[g for g in env.get('WB_SAAS_OFFSITE_ARCHIVE_GROUPS','').split(',') if g]
        groups=list(dict.fromkeys(required+history))
        if any(not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,63}',g) for g in groups):raise ValueError('invalid_groups')
        archives=[]
        for group in groups:
            files=[p for p in (root/group).glob('*.tar.gz.enc') if p.is_file() and not p.is_symlink()]
            if group in required and (not files or now-max(p.stat().st_mtime for p in files)>MAX_AGE):raise ValueError('local_backup_stale')
            archives.extend(files)
        status['local_fresh']=True
        for path in archives:
            with path.open('rb') as source:
                if source.read(9) not in (b'WBSAASBK1', b'WBSAASBK2'):raise ValueError('encrypted_archive_format_invalid')
        remote=env.get('WB_SAAS_OFFSITE_REMOTE','')
        if ':' not in remote or not remote.split(':',1)[1].strip('/') or '..' in remote.split(':',1)[1].split('/'):raise ValueError('offsite_unconfigured')
        recipient=env.get('WB_SAAS_OFFSITE_PUBLIC_RECIPIENT')
        if not recipient:raise ValueError('offsite_recipient_unconfigured')
        try:
            from scripts.backup_offsite_envelope import prepare,atomic,digest
        except ImportError:
            from backup_offsite_envelope import prepare,atomic,digest
        staging=Path(env.get('WB_SAAS_OFFSITE_STAGING_DIR','/opt/wb-saas/offsite-v2-staging')).resolve()
        outputs=prepare(root,staging,recipient,groups)
        if not outputs:raise ValueError('local_backup_stale')
        files_from=staging/('.current-files-'+uuid.uuid4().hex)
        with atomic(files_from) as output:output.write(('\n'.join(p.relative_to(staging).as_posix() for p in outputs)+'\n').encode())
        remote=remote.rstrip('/')+'/offsite-v2'
        status['envelope_version']=2;status['offsite_asymmetric_encryption']=True
        logfile=Path(env.get('WB_SAAS_OFFSITE_LOG_FILE','/var/log/wb-saas-offsite-backup.log'))
        logfile.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(logfile,os.O_WRONLY|os.O_CREAT,0o600);os.close(fd);os.chmod(logfile,0o600)
        logging=['-v','--log-file',str(logfile)]
        # Official rclone --files-from ignores other filters and silently skips
        # missing files. Use the exact validated set and recheck it afterwards.
        before={path:digest(path) for path in outputs}
        try:
            result=subprocess.run(['rclone','copy',str(staging),remote,'--files-from',str(files_from),'--retries','1','--low-level-retries','1',*logging],capture_output=True,timeout=1800)
            if result.returncode:raise ValueError('offsite_copy_failed')
            checked=subprocess.run(['rclone','check',str(staging),remote,'--files-from',str(files_from),'--one-way','--download','--retries','1','--low-level-retries','1',*logging],capture_output=True,timeout=1800)
            if checked.returncode:raise ValueError('offsite_verification_failed')
            if any(not path.is_file() or path.is_symlink() or digest(path)!=checksum for path,checksum in before.items()):raise ValueError('offsite_source_changed')
        finally:files_from.unlink(missing_ok=True)
        retention=env.get('WB_SAAS_OFFSITE_RETENTION_DAYS')
        if retention:
            if not retention.isascii() or not retention.isdigit() or not 7<=int(retention)<=3650 or env.get('WB_SAAS_OFFSITE_DELETE_CONFIRMED')!='1':raise ValueError('remote_retention_unconfirmed')
            deleted=subprocess.run(['rclone','delete',remote,'--include','**/*.tar.gz.enc','--min-age',retention+'d','--retries','1','--low-level-retries','1',*logging],capture_output=True,timeout=300)
            if deleted.returncode:raise ValueError('remote_retention_failed')
        status.update(offsite_verified=True,verified_at=time.time())
        if not status['key_escrow_confirmed']:
            status['alert']=alert({'kind':'backup_prerequisite','code':'separate_key_escrow_unconfirmed','at':now},env)
    except (ValueError,OSError,subprocess.SubprocessError) as exc:
        # Only predefined codes, never provider stderr or credential-bearing URLs.
        status['error']=str(exc) if isinstance(exc,ValueError) and str(exc) in ERROR_CODES else 'backup_transport_failed'
        status['alert']=alert({'kind':'backup_failure','code':status['error'],'at':now},env)
    write_status(status_path,status)
    print(json.dumps(status))
    return int(not status['offsite_verified'])

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--check',action='store_true')
    raise SystemExit(check_freshness() if parser.parse_args().check else run())
