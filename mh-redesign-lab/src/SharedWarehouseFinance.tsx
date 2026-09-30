import {useEffect,useRef,useState} from 'react';
import {currentShop} from './selected-shop';
import {WarehouseSubmission,warehouseRequest,type WarehouseWorkspace} from './shared-warehouse';
type SourceRow={rrd_id:number;nm_id:number;chrt_id:number|null;variant_basis:string;operation_date:string;sale_date:string;units:number;event_type:'sale'|'return';source_kind:'wb_api'|'legacy_unverified'|'seller_mismatch';document_key:string|null;preliminary:boolean};
type Source={shop_id:number;rows:SourceRow[];truncated:boolean};
type Proof={fingerprint:string;cost_rub:number;units:number;source_kind:'wb_api'|'manual_legacy';external_nm_id:number;external_chrt_id:number;event_type:'sale'|'return'};
export function SharedWarehouseFinance({workspace}:{workspace:WarehouseWorkspace}){
 const shop=currentShop(),[source,setSource]=useState<Source|null>(null),[rowId,setRowId]=useState(''),[document,setDocument]=useState(''),[attest,setAttest]=useState(false),[reason,setReason]=useState(''),[error,setError]=useState(''),[message,setMessage]=useState(''),[busy,setBusy]=useState(false),[preview,setPreview]=useState<{identity:string;body:Record<string,unknown>;proof:Proof}|null>(null);
 const submitted=useRef(new WarehouseSubmission());
 const prefix=`/${shop?.id}`;
 async function refresh(){setSource(await warehouseRequest<Source>(prefix+'/financial-source'));}
 useEffect(()=>{const abort=new AbortController();warehouseRequest<Source>(prefix+'/financial-source',undefined,undefined,abort.signal).then(setSource).catch(caught=>{if(!abort.signal.aborted)setError(caught instanceof Error?caught.message:'Источник реализации недоступен');});return()=>abort.abort();},[prefix]);
 const row=source?.rows.find(value=>String(value.rrd_id)===rowId),identity=JSON.stringify([rowId,document,attest,reason]);
 useEffect(()=>{setPreview(null);setMessage('');},[identity]);
 const write=Boolean(shop?.can_write&&workspace.can_write),manual=row?.source_kind==='legacy_unverified';
 const eligible=Boolean(row&&row.chrt_id!==null&&row.source_kind!=='seller_mismatch'&&!row.document_key);
 async function run(confirm:boolean){
  if(!write||!eligible||!row||busy)return;setBusy(true);setError('');
  try{
   if(!workspace.documents.some(value=>value.request_key===document&&value.kind===row.event_type))throw new Error('Выберите исходный документ этого магазина того же типа');
   if(manual&&(!attest||!reason.trim()))throw new Error('Для старой строки подтвердите источник вручную и укажите основание');
   const body={rrd_id:row.rrd_id,document_key:document,manual,source_proof:manual?reason.trim():null};
   if(!confirm){const proof=await warehouseRequest<Proof>(prefix+'/financial-bindings/preview',body);setPreview({identity,body,proof});}
   else{
    if(!preview||preview.identity!==identity)throw new Error('Проверьте выбранное сопоставление заново');
    const request=submitted.current.snapshot(prefix+'/financial-bindings/confirm',{...preview.body,fingerprint:preview.proof.fingerprint});
    await warehouseRequest(prefix+'/financial-bindings/confirm',request.body,request.key);submitted.current.clear();setPreview(null);setMessage(manual?'Сохранён ручной источник. Финансовый результат остаётся предварительным.':'Документ связан с подтверждённой строкой WB.');await refresh();
   }
  }catch(caught){setError(caught instanceof Error?caught.message:'Сопоставление не сохранено');}finally{setBusy(false);}
 }
 return <section aria-labelledby="shared-financial-title"><h3 id="shared-financial-title">Сопоставление FIFO с реализацией WB</h3><p>Связь исходного документа продажи или возврата с одной строкой реализации. Это уточняет финансовый расчёт и не создаёт повторного движения склада. Ручное подтверждение старого источника сохраняет предварительное качество результата.</p>
  {error&&<p role="alert" className="operation-error">{error}</p>}{message&&<p role="status">{message}</p>}{!source&&!error&&<p role="status">Загружаем исходные строки…</p>}
  {source?.truncated&&<p>Показаны последние 200 исходных строк; отсутствие строки здесь не означает отсутствие операции.</p>}
  {source&&<table><thead><tr><th>Строка</th><th>Товар / вариант</th><th>Операция</th><th>Источник</th><th>Документ</th></tr></thead><tbody>{source.rows.map(value=><tr key={value.rrd_id}><td>{value.rrd_id} · {value.sale_date||value.operation_date}</td><td>WB {value.nm_id} · {value.chrt_id??'Вариант не определён'}</td><td>{value.event_type==='sale'?'Продажа':'Возврат'} · {value.units} ед.</td><td>{value.source_kind==='wb_api'?'Подтверждённый API WB':value.source_kind==='seller_mismatch'?'Другой продавец — сопоставление запрещено':'Старый неподтверждённый источник'}{value.preliminary&&' · предварительно'}</td><td>{value.document_key?'Связь сохранена':'Нет сопоставления'}</td></tr>)}</tbody></table>}
  {write&&source&&<form className="settings-form" onSubmit={event=>{event.preventDefault();void run(false);}}><label>Строка реализации<select required value={rowId} onChange={event=>{setRowId(event.target.value);setDocument('');setAttest(false);setReason('');}}><option value="">Выберите строку</option>{source.rows.map(value=><option key={value.rrd_id} value={value.rrd_id} disabled={value.chrt_id===null||value.source_kind==='seller_mismatch'||Boolean(value.document_key)}>{value.rrd_id} · WB {value.nm_id}, вариант {value.chrt_id??'не определён'} · {value.units} ед.</option>)}</select></label>
   <label>Подтверждённый исходный документ<select required value={document} onChange={event=>setDocument(event.target.value)}><option value="">Выберите документ</option>{workspace.documents.filter(value=>value.kind===row?.event_type).map(value=><option key={value.request_key} value={value.request_key}>{value.source_ref} · {value.document_date}</option>)}</select></label>
   {manual&&<><label><input type="checkbox" checked={attest} onChange={event=>setAttest(event.target.checked)}/>Подтверждаю принадлежность старой строки выбранному продавцу по своему документу. Это ручной источник, результат остаётся предварительным.</label><label>Основание ручного подтверждения<textarea required maxLength={500} value={reason} onChange={event=>setReason(event.target.value)}/></label></>}
   <button type="submit" disabled={busy||!eligible}>Проверить финансовое сопоставление</button>
   {preview&&preview.identity===identity&&<div role="status"><p>{preview.proof.units} ед. · учтённая стоимость {preview.proof.cost_rub} ₽ · {preview.proof.source_kind==='manual_legacy'?'Ручной источник — предварительно':'Подтверждённый API WB'}</p><button type="button" disabled={busy} onClick={()=>void run(true)}>Подтвердить связь с реализацией</button></div>}
  </form>}{source&&!source.rows.length&&<p>Исходных строк продаж или возвратов пока нет.</p>}
 </section>;
}
