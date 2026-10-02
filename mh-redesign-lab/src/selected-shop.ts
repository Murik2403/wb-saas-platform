/** Selected-shop transport. Context is validated before UI mounting. */
export type SelectedShop = {id:number;name:string;verified_identity:string|null;active:boolean;snapshot_revision:number|null;can_write:boolean;snapshot_available?:boolean;allowed_actions:string[]};
type ResourceScope="shop_local"|"tenant_shared"|"forbidden";
export type ShopRoute={method:string;path:string;scope:ResourceScope;action?:string;can_write?:boolean;query_scopes?:Record<string,Record<string,ResourceScope>>;requires_kind?:boolean};
export type ShopSession = {shops:SelectedShop[];role:string;selected_shop_id:number;route_scopes:ShopRoute[]};
let selected:SelectedShop|null=null;
let session:ShopSession|null=null;
let epoch=0;
let enforced=false;
const controllers=new Set<AbortController>();
const retries=new Map<string,string>();
export function currentShop(){return selected;}
export function requireShopContext(){enforced=true;}
export function selectShop(next:ShopSession,id:number){
 const shop=next.shops.find(s=>s.id===id);
 if(!Number.isSafeInteger(id)||!shop||!shop.active)throw new Error('Магазин недоступен');
 for(const controller of controllers)controller.abort();controllers.clear();retries.clear();epoch++;session=next;selected=shop;enforced=true;
 return shop;
}
export function clearShopContext(){for(const c of controllers)c.abort();controllers.clear();retries.clear();epoch++;selected=null;session=null;}
function stillCurrent(version:number){if(epoch!==version)throw new DOMException('Магазин изменён','AbortError');}
export async function scopedFetch(input:RequestInfo|URL,init:RequestInit={}):Promise<Response>{
 if(!enforced)return globalThis.fetch(input,init);
 if(!selected||!session)throw new Error('Контекст магазина не подтверждён');
 const shop=selected,version=epoch;
 const url=new URL(String(input),window.location.origin);
 if(url.origin!==window.location.origin)throw new Error('Недопустимый адрес магазина');
 const path=url.pathname.replace(/^\/tenant-api/,'');
 const method=(init.method??'GET').toUpperCase();
 const queryShop=url.searchParams.get('shop_id');if(queryShop!==null&&queryShop!==String(shop.id))throw new Error('Магазин URL не совпадает с выбранным');
 if(typeof init.body==='string'){try{const body=JSON.parse(init.body);if(body&&typeof body==='object'&&'shop_id' in body&&body.shop_id!==shop.id)throw new Error('Магазин данных не совпадает с выбранным');}catch(reason){if(reason instanceof SyntaxError){/* Other transport bodies remain unchanged. */}else throw reason;}}
 const writes=!['GET','HEAD'].includes(method);
 const headers=new Headers(init.headers);
 const given=headers.get('X-MH-Shop-ID');if(given!==null&&given!==String(shop.id))throw new Error('Магазин запроса не совпадает с выбранным');
 headers.set('X-MH-Shop-ID',String(shop.id));
 const route=session.route_scopes.find(r=>r.method===method&&routeMatches(r.path,path));
 const explicitWrite=route?.can_write===true&&shop.allowed_actions.includes(method+' '+route.path);
 if(writes&&!shop.can_write&&!explicitWrite)throw new Error('Магазин доступен только для просмотра');
 const resolvedScope=route?resolveScope(route,url,shop.id!==0):undefined;
 const expectedScope=shop.id===0&&resolvedScope==='forbidden'?'tenant_shared':resolvedScope;
 if(shop.id!==0&&(!route||expectedScope==='forbidden'))throw new Error('Раздел не поддерживает выбранный магазин');
 if(writes&&shop.id!==0&&(route?.can_write===false||!shop.allowed_actions.includes(method+' '+(route?.path??path))))throw new Error('Действие недоступно для выбранного магазина');
 let replayIdentity:string|null=null;
 if(writes){
  if(headers.has('X-MH-Shop-Revision')&&headers.get('X-MH-Shop-Revision')!==String(shop.snapshot_revision))throw new Error('Версия запроса не совпадает с выбранным магазином');
  if(shop.snapshot_revision!==null)headers.set('X-MH-Shop-Revision',String(shop.snapshot_revision));
  // Binary/multipart payloads have no proven canonical content identity: fresh key, no automatic retry alias.
  replayIdentity=typeof init.body==='string'||init.body==null?JSON.stringify([shop.id,shop.verified_identity,shop.snapshot_revision,method,path,url.search,init.body??null]):null;
  let key=headers.get('X-MH-Request-Key');
  if(!key){key=(replayIdentity?retries.get(replayIdentity):null)??crypto.randomUUID();if(replayIdentity)retries.set(replayIdentity,key);}
  if(!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(key))throw new Error('Некорректный ключ действия');
  headers.set('X-MH-Request-Key',key);
 }
 const controller=new AbortController();controllers.add(controller);
 const abort=()=>controller.abort();init.signal?.addEventListener('abort',abort,{once:true});if(init.signal?.aborted)controller.abort();
 try{
  const response=await globalThis.fetch(input,{...init,headers,signal:controller.signal});stillCurrent(version);
  let revisionChanged=false;
  if(response.ok&&shop.id===0){
   const responseId=response.headers.get('X-MH-Shop-ID'),identity=response.headers.get('X-MH-Shop-Identity'),scope=response.headers.get('X-MH-Resource-Scope');
   if((responseId!==null&&responseId!=='0')||(identity!==null&&identity!==shop.verified_identity)||(scope!==null&&(route?scope!==expectedScope:!['shop_local','tenant_shared'].includes(scope))))throw new Error('Источник ответа не соответствует выбранному магазину');
  }
  if(response.ok&&shop.id!==0){
   const revision=response.headers.get('X-MH-Shop-Revision');
   const validRevision=revision!==null&&/^(0|[1-9][0-9]*)$/.test(revision)&&Number.isSafeInteger(Number(revision))&&Number(revision)>=Number(shop.snapshot_revision);
   revisionChanged=validRevision&&Number(revision)!==shop.snapshot_revision;
   if(response.headers.get('X-MH-Shop-ID')!==String(shop.id)||response.headers.get('X-MH-Shop-Identity')!==shop.verified_identity||!validRevision||!isFingerprint(response.headers.get('X-MH-Shop-Fingerprint'))||response.headers.get('X-MH-Resource-Scope')!==expectedScope)throw new Error('Источник ответа не соответствует выбранному магазину');
  }
  
  // Guard body decoding too: a resolved fetch is not yet a consumed response.
  const guard=(source:Response):Response=>new Proxy(source,{get(target,property){
   const value=Reflect.get(target,property,target);
   if(property==='clone')return ()=>{stillCurrent(version);return guard(target.clone());};
   if(['json','text','blob','arrayBuffer','formData'].includes(String(property)))return async()=>{stillCurrent(version);const result=await value.call(target);stillCurrent(version);if(response.ok&&replayIdentity)retries.delete(replayIdentity);if(revisionChanged)window.dispatchEvent(new Event('mh-shop-revision-changed'));return result;};
   return typeof value==='function'?value.bind(target):value;
  }});
  return guard(response);
 }finally{controllers.delete(controller);init.signal?.removeEventListener('abort',abort);}
}

export function shopReadTimeout(path:string,timeoutMs:number){
 if(!selected||selected.id===0||!session)return timeoutMs;
 const url=new URL(path,window.location.origin);
 const route=session.route_scopes.find(r=>r.method==='GET'&&routeMatches(r.path,url.pathname.replace(/^\/tenant-api/,'')));
 return route&&resolveScope(route,url,true)==='shop_local'?Math.max(timeoutMs,95000):timeoutMs;
}

function resolveScope(route:ShopRoute,url:URL,strict:boolean):ResourceScope{
 let scope=route.scope;
 if(route.requires_kind&&!url.searchParams.has('kind')){if(strict)throw new Error('Укажите вид истории выбранного магазина');return 'forbidden';}
 for(const [key,choices] of Object.entries(route.query_scopes??{})){
  const values=url.searchParams.getAll(key);
  if(values.length!==1||!Object.prototype.hasOwnProperty.call(choices,values[0])){if(strict)throw new Error('Вид истории магазина не подтверждён');return 'forbidden';}
  scope=choices[values[0]];
 }
 return scope;
}
function validQueryScopes(route:ShopRoute){
 if(route.requires_kind!==undefined&&typeof route.requires_kind!=='boolean')return false;
 if(route.query_scopes===undefined)return route.requires_kind!==true;
 if(!route.query_scopes||typeof route.query_scopes!=='object'||Array.isArray(route.query_scopes))return false;
 if(route.requires_kind&&!Object.prototype.hasOwnProperty.call(route.query_scopes,'kind'))return false;
 return Object.entries(route.query_scopes).every(([key,choices])=>/^[a-z_]+$/.test(key)&&!!choices&&typeof choices==='object'&&!Array.isArray(choices)&&Object.keys(choices).length>0&&Object.values(choices).every(scope=>['shop_local','tenant_shared','forbidden'].includes(scope)));
}

function isFingerprint(value:unknown):value is string{return typeof value==='string'&&/^[0-9a-f]{64}$/.test(value);}
function routeMatches(pattern:string,path:string){
 const parts=pattern.split('/'),actual=path.split('/');
 return parts.length===actual.length&&parts.every((part,i)=>/^\{[a-z_]+\}$/.test(part)?/^[a-zA-Z0-9_-]+$/.test(actual[i]):part===actual[i]);
}
export function validateShopSession(raw:unknown):ShopSession{
 if(!raw||typeof raw!=='object')throw new Error('Контекст магазинов недоступен');
 const data=raw as ShopSession;
 if(!Array.isArray(data.shops)||!Array.isArray(data.route_scopes)||typeof data.role!=='string'||!Number.isSafeInteger(data.selected_shop_id))throw new Error('Контекст магазинов недоступен');
 const ids=new Set<number>();
 for(const s of data.shops){
  if(!Number.isSafeInteger(s.id)||s.id<0||ids.has(s.id)||typeof s.name!=='string'||!s.name||typeof s.active!=='boolean'||typeof s.can_write!=='boolean'||!Array.isArray(s.allowed_actions)||!s.allowed_actions.every(a=>typeof a==='string')||(s.snapshot_revision!==null&&(!Number.isSafeInteger(s.snapshot_revision)||s.snapshot_revision<0))||(s.verified_identity!==null&&!isFingerprint(s.verified_identity))||(s.id!==0&&(!isFingerprint(s.verified_identity)||s.snapshot_revision===null)))throw new Error('Контекст магазинов недоступен');
  ids.add(s.id);
 }
 if(!ids.has(0)||!ids.has(data.selected_shop_id)||data.route_scopes.some(r=>!r||typeof r.method!=='string'||!(typeof r.path==='string'&&(r.path.startsWith('/api/v1/')||['/api/overview','/api/today','/api/products','/api/ads','/healthz','/_stcore/health'].includes(r.path)))||(!['shop_local','tenant_shared','forbidden'].includes(r.scope)||!validQueryScopes(r))))throw new Error('Контекст магазинов недоступен');
 return data;
}

export async function downloadScoped(url:string,filename:string){
 const r=await scopedFetch(url);if(!r.ok)throw new Error('Экспорт выбранного магазина недоступен');
 const blob=await r.blob();const objectUrl=URL.createObjectURL(blob);
 try{const link=document.createElement('a');link.href=objectUrl;link.download=filename;link.click();}finally{URL.revokeObjectURL(objectUrl);}
}

export function shopViewUrl(view:string){const url=new URL(window.location.href);url.searchParams.set('view',view);return url.pathname+url.search+url.hash;}

export function navigateToShop(id:number){
 if(!session)throw new Error('Контекст магазина не подтверждён');
 selectShop(session,id);const url=new URL(window.location.href);url.searchParams.set('shop',String(id));clearShopContext();window.location.replace(url.toString());
}

/** Only this bootstrap may change an unbound primary identity. All ordinary responses remain strict. */
export async function bindPrimaryWarehouse(base:string,headers:Record<string,string>){
 if(!selected||!session||selected.id!==0||session.role!=='owner'||selected.verified_identity!==null)throw new Error('Подтвердите основной магазин под владельцем');
 const version=epoch;
 try{
 const fixed=new URL(base+'/api/v1/shared-warehouse/primary-binding',window.location.origin);
 if(fixed.origin!==window.location.origin)throw new Error('Недопустимый адрес кабинета');
 const bindingHeaders=new Headers(headers);
 bindingHeaders.set('Content-Type','application/json');bindingHeaders.set('X-MH-Shop-ID','0');bindingHeaders.set('X-MH-Request-Key',crypto.randomUUID());
 const response=await globalThis.fetch(fixed,{method:'POST',headers:bindingHeaders,body:'{}'});stillCurrent(version);
 const value=await response.json();stillCurrent(version);
 if(!response.ok){const detail=value?.detail;const message=typeof detail==='string'?detail:detail?.message;throw new Error(typeof message==='string'&&message.length<=600?message:`Подтверждение магазина не выполнено (код ${response.status})`);}
 if(!response.ok||response.headers.get('X-MH-Shop-ID')!=='0'||response.headers.get('X-MH-Resource-Scope')!=='tenant_shared'||value?.shop_id!==0||value?.refresh_session!==true||!isFingerprint(value?.verified_identity)||response.headers.get('X-MH-Shop-Identity')!==value.verified_identity)throw new Error('Основной магазин не подтверждён; обновите страницу');
 const freshResponse=await globalThis.fetch(base+'/api/v1/shop-context/session',{headers:{...headers,'X-MH-Shop-ID':'0'}});stillCurrent(version);
 if(!freshResponse.ok||freshResponse.headers.get('X-MH-Shop-ID')!=='0'||freshResponse.headers.get('X-MH-Shop-Identity')!==value.verified_identity)throw new Error('Обновите страницу для подтверждения магазина');
 const fresh=validateShopSession(await freshResponse.json());stillCurrent(version);
 const primary=fresh.shops.find(shop=>shop.id===0);
 if(fresh.role!=='owner'||fresh.selected_shop_id!==0||!primary?.active||primary.verified_identity!==value.verified_identity)throw new Error('Контекст основного магазина изменился; обновите страницу');
 clearShopContext();window.location.replace(window.location.href);
 }catch(reason){if(epoch===version){clearShopContext();window.dispatchEvent(new CustomEvent('mh-shop-context-invalidated',{detail:reason instanceof Error?reason.message:'Подтверждение магазина не выполнено'}));}throw reason;}
}
