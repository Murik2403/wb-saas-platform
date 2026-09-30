import {useEffect,useState,type ReactNode} from 'react';
import {clearShopContext,requireShopContext,selectShop,validateShopSession,type ShopSession} from './selected-shop';
import './ShopContextBoundary.css';

export function ShopContextBoundary({children,navigate=(url:string)=>window.location.replace(url)}:{children:ReactNode;navigate?:(url:string)=>void}){
 const [session,setSession]=useState<ShopSession|null>(null),[id,setId]=useState<number|null>(null),[error,setError]=useState(''),[attempt,setAttempt]=useState(0);
 useEffect(()=>{const refresh=()=>{clearShopContext();setSession(null);setId(null);navigate(window.location.href);};window.addEventListener('mh-shop-revision-changed',refresh);return()=>window.removeEventListener('mh-shop-revision-changed',refresh);},[]);
 useEffect(()=>{
  requireShopContext();clearShopContext();setSession(null);setId(null);setError('');
  const controller=new AbortController();
  const base=import.meta.env.VITE_MH_API_BASE_URL??'/tenant-api';
  void globalThis.fetch(base+'/api/v1/shop-context/session',{credentials:'same-origin',signal:controller.signal,cache:'no-store'}).then(async r=>{
   if(!r.ok)throw new Error('Не удалось подтвердить доступ к магазинам');
   const data=validateShopSession(await r.json());if(controller.signal.aborted)return;
   const raw=new URL(window.location.href).searchParams.get('shop');
   const selected=raw===null?data.selected_shop_id:(/^(0|[1-9][0-9]*)$/.test(raw)?Number(raw):NaN);
   selectShop(data,selected);setSession(data);setId(selected);
  }).catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:'Контекст магазинов недоступен');});
  return()=>{controller.abort();clearShopContext();};
 },[attempt]);
 if(error)return <section className="panel" role="alert"><h1>Магазины временно недоступны</h1><p>{error}. Данные другого магазина не открыты.</p><button onClick={()=>setAttempt(v=>v+1)}>Повторить проверку доступа</button></section>;
 if(!session||id===null)return <section className="panel" role="status">Подтверждаем доступ к магазинам…</section>;
 const shop=session.shops.find(s=>s.id===id)!;
 function change(next:number){
  try{selectShop(session!,next);const url=new URL(window.location.href);url.searchParams.set('shop',String(next));clearShopContext();setSession(null);setId(null);setError('');navigate(url.toString());}
  catch(reason){clearShopContext();setError(reason instanceof Error?reason.message:'Магазин недоступен');}
 }
 return <><section className="panel shop-context-bar" aria-label="Контекст выбранного магазина">
  <label>Магазин кабинета <select aria-label="Магазин кабинета" value={id} onChange={e=>change(Number(e.target.value))}>{session.shops.filter(s=>s.active).map(s=><option key={s.id} value={s.id}>{s.name}</option>)}</select></label>
  <p className="shop-context-access">{shop.can_write?'Действия доступны в пределах ваших прав.':'Только просмотр.'}</p>
  <details className="shop-context-help"><summary>Что общее для магазинов</summary><p>Сырьё, физический склад, производство и денежные счета общие для кабинета. Выбор магазина не создаёт их копию; доступ определяется разделом.</p></details>
  {shop.id!==0&&shop.snapshot_available===false&&<p>История этого магазина ещё не загружена. Данные основного магазина не подставляются.</p>}
  {shop.id!==0&&<p>Поддерживаются только разделы выбранного магазина. Остальные запросы заблокированы без переключения на основной магазин.</p>}
 </section><div key={`${shop.id}:${shop.verified_identity??'primary'}:${shop.snapshot_revision??'primary'}`}>{children}</div></>;
}
