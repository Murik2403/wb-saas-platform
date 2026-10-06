"""Short-lived purpose-bound identity proof supplied by the gateway ForwardAuth."""
import hashlib,hmac,json,re,time

def payload(method,path,slug,role,account,issued):
    return json.dumps(['mh-gateway-auth-v1',method.upper(),path,slug,role,account,int(issued)],separators=(',',':'),ensure_ascii=True).encode()
def sign(secret,method,path,slug,role,account,now=None):
    if not secret:return ''
    issued=int(time.time() if now is None else now)
    return f'{issued}.'+hmac.new(secret.encode(),payload(method,path,slug,role,account,issued),hashlib.sha256).hexdigest()
def verify(secret,proof,method,path,slug,role,account,now=None):
    if not secret or not isinstance(proof,str) or not re.fullmatch(r'[0-9]{10}\.[0-9a-f]{64}',proof):return False
    issued=int(proof.split('.')[0]);clock=int(time.time() if now is None else now)
    if not 0<=clock-issued<=60:return False
    return hmac.compare_digest(sign(secret,method,path,slug,role,account,issued).encode(),proof.encode())
def request_target(scope):
    raw=scope.get('raw_path',scope.get('path','/').encode()).decode('ascii',errors='strict')
    query=scope.get('query_string',b'').decode('ascii',errors='strict')
    return raw+('?' + query if query else '')
