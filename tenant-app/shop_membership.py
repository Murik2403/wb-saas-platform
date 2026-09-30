"""Fresh, tenant-scoped membership lookup; no successful-role cache."""
import json
import re
from time import monotonic
from urllib.parse import urlsplit
import httpx
import config

ROLES={'owner','manager','viewer','technologist','cutter','seamstress','warehouse','integration_reader'}

def account_id(value):
    if not isinstance(value,str) or not re.fullmatch(r'[1-9][0-9]{0,18}',value) or int(value)>2**63-1:
        raise ValueError('Нужен подтверждённый идентификатор участника')
    return value

def current_role(account):
    account=account_id(account)
    origin=config.INTERNAL_API_URL
    parsed=urlsplit(origin)
    if (parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or
        parsed.query or parsed.fragment or parsed.path not in ('','/') or not config.INTERNAL_API_SECRET or
        not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}',config.TENANT_SLUG)):
        raise ValueError('Проверка прав планировщика недоступна')
    try:
        deadline=monotonic()+4
        with httpx.Client(timeout=1,follow_redirects=False,trust_env=False) as client:
            with client.stream('POST',origin.rstrip('/')+'/internal/tenant-member-role',
                headers={'X-Internal-Secret':config.INTERNAL_API_SECRET},
                json={'slug':config.TENANT_SLUG,'account':account}) as response:
                if response.status_code!=200:raise ValueError('Проверка прав планировщика недоступна')
                data=bytearray()
                for chunk in response.iter_bytes():
                    if monotonic()>deadline:raise ValueError('Проверка прав планировщика недоступна')
                    if len(data)+len(chunk)>1024:raise ValueError('Некорректный ответ проверки прав')
                    data.extend(chunk)
        value=json.loads(data)
        if type(value)!=dict or set(value)!={'role'} or (value['role'] is not None and (type(value['role'])!=str or value['role'] not in ROLES)):
            raise ValueError('Некорректный ответ проверки прав')
        return value['role']
    except (httpx.HTTPError,json.JSONDecodeError,UnicodeError) as exc:
        raise ValueError('Проверка прав планировщика недоступна') from exc
