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
def sign_context(secret,slug,method,uri,role,account,issued=None):
    return sign(secret,method,uri,slug,role,account,issued)

VERIFIED_SCOPE_KEY='mh.gateway-identity.verified'
VERIFIED_TOKEN=object()
class GatewayIdentityGuard:
    """Verify at ingress, before slow request guards; no authority comes from raw headers."""
    def __init__(self,app,context):self.app=app;self.context=context
    async def __call__(self,scope,receive,send):
        if scope.get('type')!='http':return await self.app(scope,receive,send)
        demo,slug,secret=self.context()
        path=scope.get('path','')
        raw=scope.get('raw_path',path.encode()).split(b'?',1)[0].lower()
        ambiguous=any(value in raw for value in (b'%3f',b'%23',b'%00')) or '..' in path.split('/') or b'%2e%2e' in raw or b'.%2e' in raw or b'%2e.' in raw
        if ambiguous:return await self.reject(scope,receive,send,'Некорректный путь запроса')
        if demo:
            scope=dict(scope);scope['headers']=[(k,v) for k,v in scope.get('headers',[]) if k.lower() not in {b'x-tenant-role',b'x-tenant-account',b'x-mh-gateway-auth'}]+[(b'x-tenant-role',b'viewer'),(b'x-tenant-account',b'demo')]
        elif slug and path not in {'/healthz','/_stcore/health'}:
            pairs=[(k.lower(),v) for k,v in scope.get('headers',[])];headers=dict(pairs)
            names={b'x-tenant-role',b'x-tenant-account',b'x-mh-gateway-auth'}
            if any(sum(k==name for k,_ in pairs)!=1 for name in names):return await self.reject(scope,receive,send,'Контекст шлюза не подтверждён')
            try:
                role=headers[b'x-tenant-role'].decode();account=headers[b'x-tenant-account'].decode()
                valid=bool(account) and verify(secret,headers[b'x-mh-gateway-auth'].decode(),scope.get('method','GET'),request_target(scope),slug,role,account)
            except (UnicodeError,ValueError):valid=False
            if not valid:return await self.reject(scope,receive,send,'Контекст шлюза не подтверждён')
            scope=dict(scope);scope[VERIFIED_SCOPE_KEY]=VERIFIED_TOKEN
        return await self.app(scope,receive,send)
    @staticmethod
    async def reject(scope,receive,send,message):
        from starlette.responses import JSONResponse
        await JSONResponse({'detail':message},status_code=403)(scope,receive,send)
