import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { clearMarketplaceContext, currentMarketplace, marketplaceFetch, selectMarketplace, validateMarketplaceSession, type MarketplaceSession } from './marketplace-context';
import { scopedFetch, selectShop, type ShopSession } from './selected-shop';

const session = (patch: Partial<MarketplaceSession> = {}): MarketplaceSession => ({ marketplace: 'ozon', role: 'owner', can_write: true, is_demo: false, authorized: true, shared_routes: [{method:'GET',path:'/api/v1/cashflow'}], ...patch });
const wb: ShopSession = {role:'owner',selected_shop_id:0,shops:[{id:0,name:'Main',verified_identity:null,snapshot_revision:null,active:true,can_write:true,allowed_actions:[]}],route_scopes:[]};
const result = () => new Response('{"ok":true}', {headers:{'X-MH-Marketplace':'ozon','X-MH-Shop-ID':'0'}});
beforeEach(()=>{clearMarketplaceContext();selectMarketplace(session());});
afterEach(()=>{clearMarketplaceContext();vi.unstubAllGlobals();});

it('requires a validated authorized session and rejects dangerous shared grants',()=>{
 for(const patch of [{authorized:false},{role:'unknown'},{role:'integration_reader'},{is_demo:true},{role:'viewer'},{shared_routes:[{method:'GET',path:'/api/v1/dashboard/finance'}]}])expect(()=>validateMarketplaceSession(session(patch))).toThrow();
 clearMarketplaceContext();expect(currentMarketplace()).toBeNull();
});
it('separates Ozon from WB even when old shared components use scopedFetch',async()=>{
 const fetch=vi.fn().mockImplementation(()=>Promise.resolve(result()));vi.stubGlobal('fetch',fetch);
 await expect(scopedFetch('/tenant-api/api/v1/dashboard/finance')).rejects.toThrow();
 await expect(marketplaceFetch('/api/v1/settings/api-status')).rejects.toThrow();
 await expect(marketplaceFetch('/api/v1/production-workspace')).rejects.toThrow();
 expect(fetch).not.toHaveBeenCalled();
 await(await scopedFetch('/tenant-api/api/v1/cashflow')).json();
 const headers=new Headers(fetch.mock.calls[0][1].headers);expect(headers.get('X-MH-Marketplace')).toBe('ozon');expect(headers.get('X-MH-Shop-ID')).toBe('0');
 expect(()=>selectShop(wb,0)).toThrow();
});
it('supports insights and prices without relaxing WB route isolation',async()=>{
 vi.stubGlobal('fetch',vi.fn().mockImplementation(()=>Promise.resolve(result())));
 for(const path of ['/api/v1/ozon-insights/products','/api/v1/ozon-prices'])expect(await(await marketplaceFetch(path)).json()).toEqual({ok:true});
 selectMarketplace(session({marketplace:'wb'}));selectShop(wb,0);
 await expect(scopedFetch('/api/v1/ozon-prices')).rejects.toThrow();
 await expect(marketplaceFetch('/api/v1/ozon-insights/products')).rejects.toThrow();
});
it('rejects conflicting headers, URL markers, body selectors and foreign origins before fetch',async()=>{
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch);
 for(const [path,init] of [
  ['/api/v1/ozon-prices',{headers:{'X-MH-Marketplace':'wb'}}],
  ['/api/v1/ozon-prices',{headers:{'X-MH-Shop-ID':'2'}}],
  ['/api/v1/ozon-prices',{headers:{'X-MH-Shop-Revision':'4'}}],
  ['/api/v1/ozon-prices?shop_id=2',{}], ['/api/v1/ozon-prices?marketplace=wb',{}],
  ['/api/v1/ozon-prices?marketplace=ozon&marketplace=ozon',{}],
  ['/api/v1/ozon-prices',{method:'POST',body:'{"marketplace":"wb"}'}],
  ['/api/v1/ozon-prices',{method:'POST',body:'{"shop_id":false}'}],
  ['https://other.invalid/api/v1/ozon-prices',{}],
 ] as Array<[string,RequestInit]>)await expect(marketplaceFetch(path,init)).rejects.toThrow();
 expect(fetch).not.toHaveBeenCalled();
});
it('does not treat demo or viewer write access as enabled',async()=>{
 const fetch=vi.fn();vi.stubGlobal('fetch',fetch);
 for(const patch of [{role:'viewer',can_write:false},{is_demo:true,can_write:false}]){selectMarketplace(session(patch));await expect(marketplaceFetch('/api/v1/ozon-prices/confirm',{method:'POST',body:'{}'})).rejects.toThrow();}
 expect(fetch).not.toHaveBeenCalled();
});
it('requires successful response provenance',async()=>{
 const fetch=vi.fn().mockResolvedValue(new Response('{}'));vi.stubGlobal('fetch',fetch);
 await expect(marketplaceFetch('/api/v1/ozon-prices')).rejects.toThrow('Источник');
 fetch.mockResolvedValue(new Response('{}',{headers:{'X-MH-Marketplace':'ozon','X-MH-Shop-ID':'2'}}));
 await expect(marketplaceFetch('/api/v1/ozon-prices')).rejects.toThrow('Источник');
});
it('aborts pending Ozon fetch and rejects late old-marketplace headers',async()=>{
 let resolve!:(response:Response)=>void;let signal!:AbortSignal;
 vi.stubGlobal('fetch',vi.fn((_input,init)=>{signal=init.signal;return new Promise<Response>(r=>{resolve=r;});}));
 const pending=marketplaceFetch('/api/v1/ozon-prices');selectMarketplace(session({marketplace:'wb'}));
 expect(signal.aborted).toBe(true);resolve(result());await expect(pending).rejects.toMatchObject({name:'AbortError'});
});
it('aborts and rejects Ozon body decoding after headers on a switch',async()=>{
 let finish!:(value:unknown)=>void;let signal!:AbortSignal;const response=result();
 response.json=()=>new Promise(r=>{finish=r;});
 vi.stubGlobal('fetch',vi.fn((_input,init)=>{signal=init.signal;return Promise.resolve(response);}));
 const body=(await marketplaceFetch('/api/v1/ozon-prices')).json();selectMarketplace(session({marketplace:'wb'}));
 expect(signal.aborted).toBe(true);finish({old:true});await expect(body).rejects.toMatchObject({name:'AbortError'});
});
it('aborts WB body decoding when marketplace changes, not only shop changes',async()=>{
 selectMarketplace(session({marketplace:'wb'}));selectShop(wb,0);
 let finish!:(value:unknown)=>void;let signal!:AbortSignal;const response=new Response('{}');response.json=()=>new Promise(r=>{finish=r;});
 vi.stubGlobal('fetch',vi.fn((_input,init)=>{signal=init.signal;return Promise.resolve(response);}));
 const body=(await scopedFetch('/api/v1/dashboard/finance')).json();selectMarketplace(session());
 expect(signal.aborted).toBe(true);finish({old:true});await expect(body).rejects.toMatchObject({name:'AbortError'});
});
it('reuses failed exact write key but never across changed body or marketplace session',async()=>{
 const fetch=vi.fn().mockRejectedValue(new Error('network'));vi.stubGlobal('fetch',fetch);
 const init={method:'POST',body:'{"price":100}'};
 for(let i=0;i<2;i++)await expect(marketplaceFetch('/api/v1/ozon-prices/confirm',init)).rejects.toThrow();
 const key=(i:number)=>new Headers(fetch.mock.calls[i][1].headers).get('X-MH-Request-Key');expect(key(0)).toBe(key(1));
 await expect(marketplaceFetch('/api/v1/ozon-prices/confirm',{...init,body:'{"price":101}'})).rejects.toThrow();expect(key(2)).not.toBe(key(0));
 selectMarketplace(session());await expect(marketplaceFetch('/api/v1/ozon-prices/confirm',init)).rejects.toThrow();expect(key(3)).not.toBe(key(0));
});
it('WB transport rejects Ozon body and query selectors',async()=>{
 selectMarketplace(session({marketplace:'wb'}));selectShop(wb,0);const fetch=vi.fn();vi.stubGlobal('fetch',fetch);
 await expect(scopedFetch('/api/v1/dashboard/finance?marketplace=ozon')).rejects.toThrow();
 await expect(scopedFetch('/api/v1/cashflow',{method:'POST',body:'{"marketplace":"ozon"}'})).rejects.toThrow();
 expect(fetch).not.toHaveBeenCalled();
});
