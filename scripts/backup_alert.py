"""Gateway SMTP handoff for sanitized backup events received on stdin."""
import argparse,json,re,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'gateway'))
import config
import mailer

CODES={'invalid_groups','local_backup_stale','offsite_backup_stale','encrypted_archive_format_invalid',
       'offsite_unconfigured','separate_key_escrow_unconfirmed','offsite_copy_failed','offsite_verification_failed',
       'remote_retention_unconfirmed','remote_retention_failed','backup_transport_failed','local_backup_failed',
       'offsite_recipient_unconfigured','offsite_public_key_invalid','offsite_envelope_invalid','offsite_source_invalid',
       'offsite_staging_invalid','offsite_staging_overlap','offsite_encryption_timeout','offsite_source_changed'}

def notify(recipient,payload):
    if not isinstance(recipient,str) or not re.fullmatch(r'[^\s@]{1,128}@[^\s@]{1,128}\.[^\s@]{1,32}',recipient):return 1
    if not isinstance(payload,dict) or payload.get('code') not in CODES:return 1
    if not config.SMTP_HOST:return 1
    text='Проверка резервного копирования MARKETSHELPER требует внимания.\nКод: '+payload['code']+'\nПроверьте статус и журнал резервирования на сервере. Письмо не содержит ключей и финансовых данных.'
    return 0 if mailer.send_email(recipient,'MARKETSHELPER: резервное копирование',text)=='accepted' else 1

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--to',required=True);args=parser.parse_args()
    try:
        raw=sys.stdin.buffer.read(4097)
        raise SystemExit(notify(args.to,json.loads(raw)) if len(raw)<=4096 else 1)
    except (ValueError,UnicodeError):raise SystemExit(1)
