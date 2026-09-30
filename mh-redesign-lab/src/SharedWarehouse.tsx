import {useEffect,useRef,useState} from 'react';
import {notifyShopDataUpdated} from './shop-data-updated';
import {SharedWarehouseFinance} from './SharedWarehouseFinance';
import {API_BASE_URL,apiHeaders} from './data-source';
import {bindPrimaryWarehouse,currentShop,navigateToShop} from './selected-shop';
import {WarehouseSubmission,warehouseRequest,warehouseWorkspace,type WarehouseWorkspace,type WarehousePreview} from './shared-warehouse';

type Action='mapping'|'reserve'|'commit'|'receipt'|'release'|'sale'|'return'|'quarantine'|'hold_release';
const labels:Record<Action,string>={mapping:'Связать вариант с производственным товаром',reserve:'Зарезервировать готовый товар',commit:'Подтвердить отправку',receipt:'Подтвердить приёмку',release:'Освободить резерв',sale:'Записать продажу',return:'Записать возврат',quarantine:'Поместить товар в карантин',hold_release:'Снять карантин'};
const today=()=>new Date().toISOString().slice(0,10);
export function SharedWarehouse(){
 const shop=currentShop();const [data,setData]=useState<WarehouseWorkspace|null>(null),[error,setError]=useState(''),[busy,setBusy]=useState(false),[message,setMessage]=useState('');
 const [action,setAction]=useState<Action>('reserve'),[product,setProduct]=useState(''),[item,setItem]=useState(''),[reservation,setReservation]=useState(''),[hold,setHold]=useState('');
 const [quantity,setQuantity]=useState('1'),[date,setDate]=useState(today),[source,setSource]=useState(''),[proof,setProof]=useState(''),[saleKey,setSaleKey]=useState(''),[layer,setLayer]=useState(''),[location,setLocation]=useState<'inbound'|'wb'>('wb'),[disposition,setDisposition]=useState<'available'|'quarantine'>('available');
 const [preview,setPreview]=useState<{identity:string;path:string;body:Record<string,unknown>;result:WarehousePreview}|null>(null);
 const [returnParts,setReturnParts]=useState<Record<number,string>>({});
 const receipt=useRef(new WarehouseSubmission());
 async function refresh(){setData(await warehouseWorkspace());}
 useEffect(()=>{const controller=new AbortController();warehouseWorkspace(controller.signal).then(setData).catch(reason=>{if(!controller.signal.aborted)setError(reason instanceof Error?reason.message:'Общий склад недоступен');});return()=>controller.abort();},[]);
 const inputIdentity=JSON.stringify([action,product,item,reservation,hold,quantity,date,source,proof,saleKey,layer,location,disposition,returnParts]);
 useEffect(()=>{setPreview(null);setMessage('');},[inputIdentity]);
 const selectedReservation=data?.reservations.find(row=>row.id===reservation);
 const selectedHold=data?.holds.find(row=>row.id===hold);
 const configured=Boolean(data&&!data.needs_primary_binding);
 const canWrite=Boolean(configured&&data?.can_write);
 function operation():{path:string;body:Record<string,unknown>;preview:boolean}{
  if(!shop||!data)throw new Error('Магазин не подтверждён');
  const units=Number(quantity);if(['reserve','sale','return','quarantine'].includes(action)&&(!Number.isSafeInteger(units)||units<=0))throw new Error('Укажите целое положительное количество');
  if(action!=='mapping'&&!/^\d{4}-\d{2}-\d{2}$/.test(date))throw new Error('Укажите дату документа');
  const picked=data.products.find(row=>`${row.nm_id}:${row.chrt_id}`===product);
  const document={document_date:date,source_ref:source.trim()};
  const prefix=`/${shop.id}`;
  if(action==='mapping'){if(!picked||!data.items.some(row=>row.id===item))throw new Error('Выберите реальный вариант и производственный товар');return {path:prefix+'/mappings',body:{nm_id:picked.nm_id,chrt_id:picked.chrt_id,item_id:item},preview:true};}
  if(!document.source_ref)throw new Error('Укажите документ — основание движения');
  if(action==='reserve'){if(!picked)throw new Error('Выберите реальный вариант товара');return {path:prefix+'/reserve',body:{nm_id:picked.nm_id,chrt_id:picked.chrt_id,quantity:units,...document},preview:true};}
  if(action==='hold_release'){if(!selectedHold||!proof.trim())throw new Error('Выберите карантин и укажите основание снятия');return {path:prefix+`/holds/${selectedHold.id}/release`,body:{...document,revision:selectedHold.revision,reason:proof.trim()},preview:false};}
  if(!selectedReservation)throw new Error('Выберите складской документ магазина');
  if(action==='sale')return {path:prefix+`/reservations/${reservation}/sale`,body:{quantity:units,...document},preview:true};
  if(action==='return'){if(!data.documents.some(row=>row.kind==='sale'&&row.request_key===saleKey&&row.reservation_id===reservation))throw new Error('Выберите исходную продажу');const parts=Object.entries(returnParts).filter(([,value])=>value.trim()).map(([id,value])=>({layer_id:Number(id),units:Number(value)}));if(parts.length&&(parts.some(row=>!Number.isSafeInteger(row.units)||row.units<=0)||parts.reduce((sum,row)=>sum+row.units,0)!==units))throw new Error('Сумма подтверждённых партий должна совпасть с количеством возврата');return {path:prefix+'/returns',body:{quantity:units,...document,revision:selectedReservation.revision,sale_request_key:saleKey,disposition,allocation_proof:proof.trim()||null,...(parts.length?{allocation_plan:parts}:{})},preview:true};}
  if(action==='quarantine'){if(!layer||!proof.trim())throw new Error('Укажите исходную партию и подтверждение карантина');return {path:prefix+'/quarantine/confirm',body:{quantity:units,...document,revision:selectedReservation.revision,reservation_id:reservation,layer_id:Number(layer),location,proof:proof.trim()},preview:false};}
  return {path:prefix+`/reservations/${reservation}/transition`,body:{...document,revision:selectedReservation.revision,kind:action},preview:false};
 }
 async function run(confirm:boolean){
  if(!canWrite||busy)return;setBusy(true);setError('');
  try{
   const current=operation();
   if(current.preview&&!confirm){const body={...current.body};if(action!=='return')delete body.source_ref;const result=await warehouseRequest<WarehousePreview>(current.path+'/preview',body);setPreview({identity:inputIdentity,path:current.path,body:current.body,result});}
   else{
    if(current.preview&&(!preview||preview.identity!==inputIdentity))throw new Error('Сначала проверьте текущее движение');
    const body={...current.body,...(current.preview&&action!=='return'?{fingerprint:preview?.result.fingerprint}:{})};
    const path=current.path+(current.preview?'/confirm':'');const submitted=receipt.current.snapshot(path,body);
    await warehouseRequest(path,submitted.body,submitted.key);notifyShopDataUpdated(shop);receipt.current.clear();setPreview(null);setMessage('Движение сохранено по исходному документу.');await refresh();
   }
  }catch(reason){setError(reason instanceof Error?reason.message:'Движение не сохранено');}finally{setBusy(false);}
 }
 async function createItem(form:HTMLFormElement){if(!data?.can_configure||shop?.id!==0)return;const values=new FormData(form);const picked=data.products.find(row=>`${row.nm_id}:${row.chrt_id}`===String(values.get('variant')));if(!picked)return;setBusy(true);setError('');try{const request=receipt.current.snapshot('/items',{nm_id:picked.nm_id,chrt_id:picked.chrt_id,name:String(values.get('name')).trim()});await warehouseRequest('/items',request.body,request.key);receipt.current.clear();await refresh();setMessage('Производственный товар зарегистрирован.');}catch(reason){setError(reason instanceof Error?reason.message:'Не удалось зарегистрировать товар');}finally{setBusy(false);}}
 return <section className="work-section" aria-labelledby="shared-stock-title"><h2 id="shared-stock-title">Общий производственный склад · {shop?.name}</h2><p>Физические партии общие для кабинета. Ниже — документы и доля выбранного магазина; они не создают вторую копию остатков и не отправляют операции в WB.</p>
  {error&&<p role="alert" className="operation-error">{error}</p>}{message&&<p role="status">{message}</p>}{!data&&<p role="status">Загружаем общий склад…</p>}
  {data?.needs_primary_binding&&<div><p>Владелец должен подтвердить основной WB-магазин перед распределением общего склада.</p><button disabled={busy||!data.can_configure} onClick={()=>{setBusy(true);void bindPrimaryWarehouse(API_BASE_URL,apiHeaders(true)).catch(reason=>{setError(String(reason.message));setBusy(false);});}} type="button">Подтвердить основной магазин</button></div>}
  {configured&&!canWrite&&<p>Для вашей роли доступны только документы и остатки. Изменение склада недоступно.</p>}
  {configured&&data?.can_configure&&(shop?.id===0?<details><summary>Производственный каталог общего склада</summary><form onSubmit={event=>{event.preventDefault();void createItem(event.currentTarget);}}><label>Реальный вариант основного товара<select name="variant" required>{data.products.map(row=><option key={`${row.nm_id}:${row.chrt_id}`} value={`${row.nm_id}:${row.chrt_id}`}>{row.product_name} · {row.variant_label} · WB {row.nm_id}</option>)}</select></label><label>Название производственного товара<input name="name" required maxLength={120}/></label><button disabled={busy} type="submit">Зарегистрировать товар</button></form></details>:<button type="button" onClick={()=>navigateToShop(0)}>Настроить производственный каталог в основном магазине</button>)}
  {configured&&data&&<><h3>Документы магазина</h3>{data.reservations.length?<table><thead><tr><th>Товар</th><th>Документ</th><th>Количество</th><th>Состояние</th></tr></thead><tbody>{data.reservations.map(row=><tr key={row.id}><td>{row.item_name} · WB {row.external_nm_id}, вариант {row.external_chrt_id}</td><td>{row.source_ref} · {row.document_date}</td><td>{row.quantity}</td><td>{row.status}</td></tr>)}</tbody></table>:<p>Складских документов этого магазина пока нет.</p>}
   <h3>Доля магазина в физических партиях</h3><table><thead><tr><th>Партия</th><th>В пути</th><th>На WB</th><th>Учтённая стоимость</th></tr></thead><tbody>{data.ownership.map(row=><tr key={`${row.reservation_id}:${row.layer_id}`}><td>{row.layer_id}</td><td>{row.inbound_units}</td><td>{row.wb_units}</td><td>{Number.isFinite(row.inbound_cost_rub)&&Number.isFinite(row.wb_cost_rub)?`${(row.inbound_cost_rub+row.wb_cost_rub).toLocaleString('ru-RU')} ₽`:'Неизвестна'}</td></tr>)}</tbody></table>
   <p>Если ответ потерялся, повторите действие с теми же полями на этой странице. После перезагрузки сначала проверьте историю документов: автоматическое восстановление ключа предыдущего действия не выполняется.</p>
   <details><summary>История оснований и карантина</summary>{data.documents.map(row=><article key={row.request_key}><p>{row.document_date} · {row.source_ref} · {row.kind}</p><small>Ключ подтверждения: {row.request_key}</small>{Array.isArray(row.result.allocations)&&row.result.allocations.map((value,index)=>{const allocation=value as {layer_id:number;units:number;cost_rub:number};return <p key={index}>Партия {allocation.layer_id}: {allocation.units} ед. · {allocation.cost_rub} ₽</p>;})}</article>)}{data.holds.map(row=><p key={row.id}>Партия {row.layer_id}: {row.units} ед. · {row.status} · {row.proof}</p>)}</details>
   {canWrite&&<form className="settings-form" onSubmit={event=>{event.preventDefault();void run(false);}}>
    <h3>Новое движение по документу</h3><label>Операция<select value={action} onChange={event=>setAction(event.target.value as Action)}>{Object.entries(labels).filter(([key])=>key!=='mapping'||data.can_configure).map(([key,label])=><option value={key} key={key}>{label}</option>)}</select></label>
    {['mapping','reserve'].includes(action)&&<label>Вариант выбранного магазина<select value={product} onChange={event=>setProduct(event.target.value)} required><option value="">Выберите вариант</option>{data.products.map(row=><option key={`${row.nm_id}:${row.chrt_id}`} value={`${row.nm_id}:${row.chrt_id}`}>{row.product_name} · {row.variant_label} · WB {row.nm_id}, вариант {row.chrt_id}</option>)}</select></label>}
    {action==='mapping'&&<label>Товар общего производства<select value={item} onChange={event=>setItem(event.target.value)} required><option value="">Выберите товар</option>{data.items.map(row=><option key={row.id} value={row.id}>{row.name} · WB {row.nm_id}, вариант {row.chrt_id}</option>)}</select></label>}
    {!['mapping','reserve','hold_release'].includes(action)&&<label>Исходный складской документ<select value={reservation} onChange={event=>setReservation(event.target.value)} required><option value="">Выберите документ</option>{data.reservations.map(row=><option key={row.id} value={row.id}>{row.source_ref} · {row.item_name} · {row.status}</option>)}</select></label>}
    {action==='hold_release'&&<label>Карантин<select value={hold} onChange={event=>setHold(event.target.value)} required><option value="">Выберите карантин</option>{data.holds.filter(row=>row.status==='held').map(row=><option key={row.id} value={row.id}>Партия {row.layer_id} · {row.units} ед. · {row.proof}</option>)}</select></label>}
    {['reserve','sale','return','quarantine'].includes(action)&&<label>Количество<input inputMode="numeric" type="number" min="1" step="1" value={quantity} onChange={event=>setQuantity(event.target.value)} required/></label>}
    {action!=='mapping'&&<><label>Дата документа<input type="date" value={date} onInput={event=>setDate(event.currentTarget.value)} onChange={event=>setDate(event.target.value)} required/></label><label>Документ — основание<input value={source} onChange={event=>setSource(event.target.value)} required maxLength={120}/></label></>}
    {action==='return'&&<><label>Исходная продажа<select value={saleKey} onChange={event=>{setSaleKey(event.target.value);setReturnParts({});}} required><option value="">Выберите продажу</option>{data.documents.filter(row=>row.kind==='sale'&&row.reservation_id===reservation).map(row=><option key={row.request_key} value={row.request_key}>{row.source_ref} · {row.document_date}</option>)}</select></label><label>Куда вернуть<select value={disposition} onChange={event=>setDisposition(event.target.value as typeof disposition)}><option value="available">Доступный остаток</option><option value="quarantine">Карантин</option></select></label><p>Для частичного возврата из нескольких партий укажите подтверждённый состав и основание. Оставьте состав пустым только если исходный документ позволяет однозначное восстановление.</p>{data.ownership.filter(row=>row.reservation_id===reservation).map(row=><label key={row.layer_id}>Возврат из партии {row.layer_id}<input type="number" min="1" step="1" value={returnParts[row.layer_id]??''} onChange={event=>setReturnParts(old=>({...old,[row.layer_id]:event.target.value}))}/></label>)}</>}
    {action==='quarantine'&&<label>Подтверждённая исходная партия<select value={layer} onChange={event=>setLayer(event.target.value)}><option value="">Не указана (проверит сервер)</option>{data.ownership.filter(row=>row.reservation_id===reservation).map(row=><option key={row.layer_id} value={row.layer_id}>Партия {row.layer_id}</option>)}</select></label>}
    {action==='quarantine'&&<label>Местонахождение<select value={location} onChange={event=>setLocation(event.target.value as typeof location)}><option value="wb">На WB</option><option value="inbound">В пути</option></select></label>}
    {['return','quarantine','hold_release'].includes(action)&&<label>Подтверждение партии / основание<textarea value={proof} onChange={event=>setProof(event.target.value)} maxLength={500}/></label>}
    <button type="submit" disabled={busy}>{busy?'Проверяем…':['mapping','reserve','sale','return'].includes(action)?'Проверить движение':'Сохранить движение'}</button>
    {preview&&preview.identity===inputIdentity&&<div role="status"><strong>Предварительная проверка</strong><p>{labels[action]} · {quantity} ед. · {source||'Соответствие товара'}</p>{preview.result.missing_units!==undefined&&<p>Недостаёт: {preview.result.missing_units} ед.</p>}<p>Дата: {date} · магазин: {shop?.name} · {action==='return'?(disposition==='quarantine'?'Возврат в карантин':'Возврат в доступный остаток'):action==='quarantine'?location:''}</p><p>{['mapping','reserve'].includes(action)?`WB ${product.replace(':',', вариант ')}`:selectedReservation?`WB ${selectedReservation.external_nm_id}, вариант ${selectedReservation.external_chrt_id}`:''}</p>{preview.result.cost_rub!==undefined&&<p>Учтённая стоимость: {preview.result.cost_rub} ₽</p>}{(preview.result.layers??preview.result.allocations)?.map(row=><p key={row.layer_id}>Партия {row.layer_id}: {row.units} ед. · {row.cost_rub} ₽</p>)}<button type="button" disabled={busy||Number(preview.result.missing_units??0)>0} onClick={()=>void run(true)}>Подтвердить проверенное движение</button></div>}
   </form>}<SharedWarehouseFinance workspace={data}/>
  </>}
 </section>;
}
