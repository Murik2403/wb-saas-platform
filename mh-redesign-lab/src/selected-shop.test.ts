import actualRegistry from './test-fixtures/shop-route-scopes-stage4.json';
import {beforeEach,afterEach,describe,it,expect,vi} from 'vitest';
import {selectShop,clearShopContext,scopedFetch,validateShopSession,shopViewUrl,shopReadTimeout,type ShopSession} from './selected-shop';
const identity='a'.repeat(64);
export const fixture=():ShopSession=>({role:'owner',selected_shop_id:0,shops:[{id:0,name:'Primary',verified_identity:null,snapshot_revision:null,active:true,can_write:true,allowed_actions:[]},{id:2,name:'Second',verified_identity:identity,snapshot_revision:4,active:true,can_write:true,allowed_actions:['POST /api/v1/local']}],route_scopes:[{method:'GET',path:'/api/v1/local',scope:'shop_local'},{method:'POST',path:'/api/v1/local',scope:'shop_local',can_write:true},{method:'GET',path:'/api/v1/forbidden',scope:'forbidden'},{method:'GET',path:'/api/v1/shared',scope:'tenant_shared'}]});
function response(body:unknown={},id=2,rev=4){return new Response(JSON.stringify(body),{headers:{'content-type':'application/json','X-MH-Shop-ID':String(id),'X-MH-Shop-Identity':identity,'X-MH-Shop-Revision':String(rev),'X-MH-Shop-Fingerprint':'b'.repeat(64),'X-MH-Resource-Scope':'shop_local'}});}
beforeEach(()=>{clearShopContext();selectShop(fixture(),2);});afterEach(()=>{clearShopContext();vi.unstubAllGlobals();});
describe('selected-shop transport',()=>{
 it('validates primary null and rejects malformed/unknown identities without fallback',()=>{expect(validateShopSession(fixture()).shops[0].id).toBe(0);expect(()=>selectShop(fixture(),99)).toThrow();const bad=fixture();bad.shops[1].verified_identity=null;expect(()=>validateShopSession(bad)).toThrow();});
 it('stamps exact shop and rejects unsupported paths and conflicting URL/body/header',async()=>{const f=vi.fn().mockResolvedValue(response());vi.stubGlobal('fetch',f);await scopedFetch('/tenant-api/api/v1/local');expect(new Headers(f.mock.calls[0][1].headers).get('X-MH-Shop-ID')).toBe('2');await expect(scopedFetch('/tenant-api/api/v1/unknown')).rejects.toThrow();await expect(scopedFetch('/tenant-api/api/v1/local?shop_id=0')).rejects.toThrow();await expect(scopedFetch('/tenant-api/api/v1/local',{headers:{'X-MH-Shop-ID':'0'}})).rejects.toThrow();await expect(scopedFetch('/tenant-api/api/v1/local',{method:'POST',body:'{"shop_id":0}'})).rejects.toThrow();expect(f).toHaveBeenCalledTimes(1);});
 it('refuses mismatched secondary provenance and read-only mutations',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response({},0)));await expect(scopedFetch('/tenant-api/api/v1/local')).rejects.toThrow('Источник');const s=fixture();s.shops[1].can_write=false;selectShop(s,2);await expect(scopedFetch('/tenant-api/api/v1/local',{method:'POST',body:'{}'})).rejects.toThrow('просмотр');});
 it('aborts requests and discards late response when a transport ignores abort',async()=>{let resolve!:(r:Response)=>void;const f=vi.fn((_input:RequestInfo|URL,_init?:RequestInit)=>new Promise<Response>(r=>resolve=r));vi.stubGlobal('fetch',f);const pending=scopedFetch('/tenant-api/api/v1/local');selectShop(fixture(),0);expect(f.mock.calls[0][1]!.signal!.aborted).toBe(true);resolve(response());await expect(pending).rejects.toMatchObject({name:'AbortError'});});
 it('guards asynchronous body after switching, not just fetch resolution',async()=>{const real=response();let resolve!:(r:unknown)=>void;vi.spyOn(real,'json').mockImplementation(()=>new Promise(r=>resolve=r));vi.stubGlobal('fetch',vi.fn().mockResolvedValue(real));const r=await scopedFetch('/tenant-api/api/v1/local');const body=r.json();selectShop(fixture(),0);resolve({private:'old store'});await expect(body).rejects.toMatchObject({name:'AbortError'});});
 it('keeps UUID for exact failed retry; changed payload and switched shop have new UUID',async()=>{const f=vi.fn().mockRejectedValue(new Error('network'));vi.stubGlobal('fetch',f);const options={method:'POST',body:'{"cost":12}'};await expect(scopedFetch('/tenant-api/api/v1/local',options)).rejects.toThrow();await expect(scopedFetch('/tenant-api/api/v1/local',options)).rejects.toThrow();const keys=f.mock.calls.map(c=>new Headers(c[1].headers).get('X-MH-Request-Key'));expect(keys[0]).toBe(keys[1]);await expect(scopedFetch('/tenant-api/api/v1/local',{...options,body:'{"cost":13}'})).rejects.toThrow();expect(new Headers(f.mock.calls[2][1].headers).get('X-MH-Request-Key')).not.toBe(keys[0]);selectShop(fixture(),0);await expect(scopedFetch('/tenant-api/api/v1/local',options)).rejects.toThrow();expect(new Headers(f.mock.calls[3][1].headers).get('X-MH-Request-Key')).not.toBe(keys[0]);});
 it('newer current revision asks for session refresh after consumed receipt, no downgrade',async()=>{const listener=vi.fn();window.addEventListener('mh-shop-revision-changed',listener);vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response({bridge:{applied_revision:4}},2,6)));const r=await scopedFetch('/tenant-api/api/v1/local',{method:'POST',body:'{}'});expect(listener).not.toHaveBeenCalled();expect(await r.json()).toEqual({bridge:{applied_revision:4}});expect(listener).toHaveBeenCalledTimes(1);window.removeEventListener('mh-shop-revision-changed',listener);});
});

it('different binary uploads never share replay identity and clone decoding remains scoped',async()=>{const f=vi.fn().mockRejectedValue(new Error('network'));vi.stubGlobal('fetch',f);for(const text of ['A','B']){const form=new FormData();form.append('file',new Blob([text]),'input.csv');await expect(scopedFetch('/tenant-api/api/v1/local',{method:'POST',body:form})).rejects.toThrow();}expect(new Headers(f.mock.calls[0][1].headers).get('X-MH-Request-Key')).not.toBe(new Headers(f.mock.calls[1][1].headers).get('X-MH-Request-Key'));f.mockResolvedValue(response());const r=(await scopedFetch('/tenant-api/api/v1/local')).clone();selectShop(fixture(),0);await expect(r.json()).rejects.toMatchObject({name:'AbortError'});});

it('primary legacy no headers stays compatible but explicit foreign provenance is rejected',async()=>{selectShop(fixture(),0);const f=vi.fn().mockResolvedValue(new Response('{}'));vi.stubGlobal('fetch',f);expect(await (await scopedFetch('/tenant-api/api/v1/local')).json()).toEqual({});f.mockResolvedValue(response({},2));await expect(scopedFetch('/tenant-api/api/v1/local')).rejects.toThrow('Источник');});

it('navigation changes view while preserving selected shop and period',()=>{window.history.replaceState(null,'','/?shop=2&period=custom&start=2026-09-01&view=settings');const url=new URL(shopViewUrl('reports'),window.location.origin);expect(url.searchParams.get('shop')).toBe('2');expect(url.searchParams.get('start')).toBe('2026-09-01');expect(url.searchParams.get('view')).toBe('reports');window.history.replaceState(null,'','/');});


it('resolves cost vs production history response scope by exact validated kind',async()=>{
 const s=fixture();s.route_scopes.push({method:'GET',path:'/api/v1/settings/change-history',scope:'shop_local',requires_kind:true,query_scopes:{kind:{cost:'shop_local',production_norm:'tenant_shared'}}});selectShop(validateShopSession(s),2);
 const f=vi.fn().mockResolvedValue(response());vi.stubGlobal('fetch',f);
 expect(await(await scopedFetch('/tenant-api/api/v1/settings/change-history?kind=cost')).json()).toEqual({});
 const shared=response();shared.headers.set('X-MH-Resource-Scope','tenant_shared');f.mockResolvedValue(shared);
 expect(await(await scopedFetch('/tenant-api/api/v1/settings/change-history?kind=production_norm')).json()).toEqual({});
 f.mockResolvedValue(response());await expect(scopedFetch('/tenant-api/api/v1/settings/change-history?kind=production_norm')).rejects.toThrow('Источник');
 const calls=f.mock.calls.length;
 for(const query of ['', '?kind=cost&kind=production_norm','?kind=unknown'])await expect(scopedFetch('/tenant-api/api/v1/settings/change-history'+query)).rejects.toThrow();
 expect(f).toHaveBeenCalledTimes(calls);
});
it('rejects malformed query scope metadata at bootstrap',()=>{
 const s=fixture();s.route_scopes.push({method:'GET',path:'/api/v1/settings/change-history',scope:'shop_local',requires_kind:true});expect(()=>validateShopSession(s)).toThrow();
 s.route_scopes.at(-1)!.query_scopes={kind:{cost:'shop_local'}};expect(validateShopSession(s)).toBe(s);
 (s.route_scopes.at(-1)!.query_scopes!.kind as Record<string,string>).cost='unsupported';expect(()=>validateShopSession(s)).toThrow();
});

it('extends only verified secondary local GET deadline including query-aware history',()=>{
 const s=fixture();s.route_scopes.push({method:'GET',path:'/api/v1/settings/change-history',scope:'shop_local',requires_kind:true,query_scopes:{kind:{cost:'shop_local',production_norm:'tenant_shared'}}});selectShop(s,2);
 expect(shopReadTimeout('/api/v1/local',20000)).toBe(95000);expect(shopReadTimeout('/api/v1/local',120000)).toBe(120000);
 expect(shopReadTimeout('/api/v1/shared',20000)).toBe(20000);expect(shopReadTimeout('/api/v1/forbidden',20000)).toBe(20000);
 expect(shopReadTimeout('/api/v1/settings/change-history?kind=cost',60000)).toBe(95000);expect(shopReadTimeout('/api/v1/settings/change-history?kind=production_norm',20000)).toBe(20000);
 expect(()=>shopReadTimeout('/api/v1/settings/change-history',20000)).toThrow();selectShop(s,0);expect(shopReadTimeout('/api/v1/local',20000)).toBe(20000);
});

it('accepts exact actual backend route registry including trusted legacy overview and health routes',()=>{
 const s=fixture();s.route_scopes=actualRegistry as ShopSession['route_scopes'];expect(validateShopSession(s)).toBe(s);
 expect(s.route_scopes.find(r=>r.path==='/api/overview')).toBeDefined();expect(s.route_scopes.find(r=>r.path==='/healthz')).toBeDefined();
 const bad=fixture();bad.route_scopes.push({method:'GET',path:'/api/not-a-trusted-legacy-path',scope:'tenant_shared'});expect(()=>validateShopSession(bad)).toThrow();
});

it('keeps primary combined history and secondary-forbidden routes compatible with actual server shared headers',async()=>{
 const s=fixture();s.route_scopes=actualRegistry as ShopSession['route_scopes'];s.route_scopes.push({method:'POST',path:'/api/v1/primary-action',scope:'forbidden'});selectShop(s,0);
 const f=vi.fn().mockImplementation(()=>Promise.resolve(new Response('{}',{headers:{'X-MH-Shop-ID':'0','X-MH-Resource-Scope':'tenant_shared'}})));vi.stubGlobal('fetch',f);
 expect(await(await scopedFetch('/api/v1/settings/change-history')).json()).toEqual({});
 expect(await(await scopedFetch('/api/v1/primary-action',{method:'POST',body:'{}'})).json()).toEqual({});
 selectShop(s,2);await expect(scopedFetch('/api/v1/primary-action',{method:'POST',body:'{}'})).rejects.toThrow();
});

it('accepts every actual primary registry route with server-compatible resource scope',async()=>{
 const s=fixture();s.route_scopes=actualRegistry as ShopSession['route_scopes'];selectShop(s,0);
 const f=vi.fn();vi.stubGlobal('fetch',f);
 for(const route of s.route_scopes){
  const path=route.path.replace(/\{[a-z_]+\}/g,'1');
  const expected=route.scope==='forbidden'||route.requires_kind?'tenant_shared':route.scope;
  f.mockResolvedValue(new Response('{}',{headers:{'X-MH-Shop-ID':'0','X-MH-Resource-Scope':expected}}));
  expect(await(await scopedFetch(path,{method:route.method,...(!['GET','HEAD'].includes(route.method)?{body:'{}'}:{})})).json()).toEqual({});
 }
 expect(f).toHaveBeenCalledTimes(s.route_scopes.length);
});
