"""Derive scoped tenant credentials without distributing the master key."""
import hashlib
import hmac
import re
import base64
import json

def provisioner_credential(master: str) -> str:
    if not master:return ''
    return 'v1.'+hmac.new(master.encode(),b'provisioner-control:v1',hashlib.sha256).hexdigest()

def tenant_keyring(raw: str, slug: str) -> str:
    """Only a namespaced retained ring may cross into one tenant container."""
    if not raw:return ''
    if not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}',slug) or len(raw.encode())>131072:raise ValueError('Invalid tenant keyring namespace')
    try:value=json.loads(raw)
    except ValueError:raise ValueError('Invalid tenant keyring format') from None
    if not isinstance(value,dict) or any(not isinstance(key,str) or not re.fullmatch(r'[a-z0-9][a-z0-9-]{0,62}',key) or not isinstance(ring,dict) for key,ring in value.items()):
        raise ValueError('Retained keys require explicit tenant namespaces; global flat keyrings are forbidden')
    selected=value.get(slug,{})
    for key_id,key in selected.items():
        if not isinstance(key_id,str) or not re.fullmatch(r'[A-Za-z0-9_.-]{1,64}',key_id) or not isinstance(key,str):raise ValueError('Invalid retained tenant key')
        try:decoded=base64.b64decode(key.encode(),altchars=b'-_',validate=True)
        except (ValueError,UnicodeError):raise ValueError('Invalid retained tenant key') from None
        if len(decoded)!=32 or base64.urlsafe_b64encode(decoded).decode()!=key:raise ValueError('Invalid retained tenant key')
    encoded=json.dumps(selected,separators=(',',':'))
    if len(encoded)>8192:raise ValueError('Retained tenant keyring exceeds safe size')
    return encoded if selected else ''


def tenant_credential(master: str, slug: str) -> str:
    if not master or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", slug):
        return ""
    return "v1." + hmac.new(master.encode(), ("tenant-internal:" + slug).encode(), hashlib.sha256).hexdigest()


def verify_credential(master: str, slug: str, provided: str) -> bool:
    expected = tenant_credential(master, slug)
    return bool(expected) and hmac.compare_digest(expected.encode(), provided.encode())


def tenant_data_key(master: str, slug: str) -> str:
    """Derive a Fernet-compatible encryption key without exposing ``master``."""
    if not master or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,62}", slug):
        return ""
    digest = hmac.new(
        master.encode(), ("tenant-data-encryption:v1:" + slug).encode(), hashlib.sha256
    ).digest()
    return base64.urlsafe_b64encode(digest).decode()
