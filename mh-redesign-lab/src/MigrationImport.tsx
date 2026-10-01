import { scopedFetch as fetch } from "./selected-shop";
import {useEffect,useRef,useState} from "react";
import {API_BASE_URL,apiHeaders} from "./data-source";
import "./MigrationImport.css";
type Kind="products"|"materials"|"opening_materials"|"finished_lots"|"historical_documents"|"historical_attachments";
const fields:Record<string,string[]>={products:["source_id","nm_id","supplier_article","product_name"],materials:["source_id","material_name","unit"],opening_materials:["source_id","material_name","unit","quantity","unit_cost_rub","source_date"],finished_lots:["source_id","nm_id","chrt_id","source_date","cutover_date","location","original_units","remaining_units","unit_cost_rub","remaining_cost_rub","document_proof"],historical_documents:["source_id","document_id","rrd_id","nm_id","chrt_id","operation_date","sequence","event_type","quantity","lot_source_id","allocation_units","original_sale_document","retail_amount","ppvz_for_pay","commission","delivery_rub","storage_fee","deduction","penalty","acceptance","document_proof"]};
fields.historical_attachments=[...fields.historical_documents,'source_srid','financial_fingerprint'];
const labels:Record<string,string>={source_srid:"SRID исходной строки WB",financial_fingerprint:"Версия исходной строки WB (из шаблона)",source_id:"ID исходной строки / документа",nm_id:"Артикул WB",supplier_article:"Артикул продавца",product_name:"Название товара",material_name:"Название материала",unit:"Базовая единица",quantity:"Остаток в базовых единицах",unit_cost_rub:"Стоимость базовой единицы, ₽",source_date:"Дата исходной партии",chrt_id:"ID варианта WB (0 только без размеров)",cutover_date:"Дата перехода (начальный остаток)",location:"Место ready / inbound / wb",original_units:"Исходное количество партии",remaining_units:"Остаток на дату перехода",remaining_cost_rub:"Стоимость остатка, ₽",document_proof:"Исходный документ и основание",document_id:"ID финансового документа",rrd_id:"Исходный ID строки реализации WB",operation_date:"Дата операции до перехода",sequence:"Явный порядок документа в дате",event_type:"sale / return",lot_source_id:"ID исходной партии",allocation_units:"Количество из этой партии",original_sale_document:"ID исходной продажи (для sale — минус)",retail_amount:"Выручка с исходным знаком, ₽",ppvz_for_pay:"Выплата с исходным знаком, ₽",commission:"Комиссия со знаком источника, ₽",delivery_rub:"Логистика со знаком источника, ₽",storage_fee:"Хранение со знаком источника, ₽",deduction:"Удержания со знаком источника, ₽",penalty:"Штраф со знаком источника, ₽",acceptance:"Приёмка со знаком источника, ₽"};
type Input={kind:Kind;csv_text:string;mapping:Record<string,string>};
type UnitConversion={entered_quantity:string;entered_unit:string;entered_unit_price_rub:string;conversion_factor:string;base_quantity:string;base_unit:string;base_unit_price_rub:string;source_total_rub:string};
type Preview={rows:Array<{line:number;data:Record<string,string|number|UnitConversion>}>;errors:Array<{line:number;message:string}>;can_confirm:boolean;preview_fingerprint:string};
function conversionProof(value:unknown):UnitConversion|null{
  if(!value||typeof value!=="object")return null;
  const proof=value as UnitConversion;
  return [proof.entered_quantity,proof.entered_unit,proof.entered_unit_price_rub,proof.conversion_factor,proof.base_quantity,proof.base_unit,proof.base_unit_price_rub,proof.source_total_rub].every(field=>typeof field==="string"&&field.length>0)?proof:null;
}
async function call<T>(path:string,body?:unknown):Promise<T>{
  const response=await fetch(`${API_BASE_URL}/api/v1/migration-import${path}`,{method:body?"POST":"GET",headers:apiHeaders(!!body),body:body?JSON.stringify(body):undefined});
  if(!response.ok){const payload=await response.json().catch(()=>({})) as {detail?:unknown};throw new Error(typeof payload.detail==="string"?payload.detail:"Импорт не выполнен. Проверьте данные и доступ.");}
  return response.json() as Promise<T>;
}
export function MigrationImport({role,onRefresh}:{role:string;onRefresh:()=>Promise<void>}){
  const [kind,setKind]=useState<Kind>("products");const [text,setText]=useState("");
  const [mapping,setMapping]=useState<Record<string,string>>({});const [preview,setPreview]=useState<Preview|null>(null);
  const [attachmentStart,setAttachmentStart]=useState(new Date().toISOString().slice(0,10));const [attachmentEnd,setAttachmentEnd]=useState(new Date().toISOString().slice(0,10));
  const [busy,setBusy]=useState(false);const [message,setMessage]=useState("");
  const [consent,setConsent]=useState(false);
  const [history,setHistory]=useState<Array<{request_key:string;kind:Kind;imported_count:number;created_at:string}>>([]);
  const pending=useRef<(Input&{request_key:string;preview_fingerprint:string})|null>(null);
  const reviewEpoch=useRef(0);
  const lock=useRef(false);const canWrite=role==="owner"||role==="manager";
  const header=text.split(/\r?\n/,1)[0]??"";const delimiter=header.includes("\t")?"\t":header.includes(";")?";":",";
  const columns:string[]=[];let cell="",quoted=false;
  for(let i=0;i<header.length;i++){const ch=header[i];if(ch==='"'){if(quoted&&header[i+1]==='"'){cell+='"';i++;}else quoted=!quoted;}else if(ch===delimiter&&!quoted){columns.push(cell.replace(/^\uFEFF/,""));cell="";}else cell+=ch;}
  columns.push(cell.replace(/^\uFEFF/,""));
  function change(){reviewEpoch.current++;setPreview(null);pending.current=null;setConsent(false);setMessage("");}
  useEffect(()=>{reviewEpoch.current++;setPreview(null);pending.current=null;setConsent(false);setMessage("");},[role]);
  function template(){
    const sample:Record<string,string>={products:"source_id;nm_id;supplier_article;product_name\nproduct-1;123456;ARTICLE;Название товара",materials:"source_id;material_name;unit\nmaterial-1;Ткань импорт;м",opening_materials:"source_id;material_name;unit;quantity;unit_cost_rub;source_date\nopening-1;Ткань импорт;м;10;125.5;2026-09-01",finished_lots:"source_id;nm_id;chrt_id;source_date;cutover_date;location;original_units;remaining_units;unit_cost_rub;remaining_cost_rub;document_proof\nlot-example;123456;0;2026-08-01;2026-09-01;ready;10;5;10;50;Документ партии и остаток проверены",historical_documents:"source_id;document_id;rrd_id;nm_id;chrt_id;operation_date;sequence;event_type;quantity;lot_source_id;allocation_units;original_sale_document;retail_amount;ppvz_for_pay;commission;delivery_rub;storage_fee;deduction;penalty;acceptance;document_proof\nallocation-sale;sale-example;1001;123456;0;2026-08-02;1;sale;10;lot-example;10;-;200;180;20;0;0;0;0;0;Документ продажи проверен\nallocation-return;return-example;1002;123456;0;2026-08-03;1;return;5;lot-example;5;sale-example;-100;-90;-10;5;0;0;0;0;Возврат связан с исходной продажей"};
    setText(sample[kind]??fields[kind].join(";"));setMapping(Object.fromEntries(fields[kind].map(field=>[field,field])));change();
  }
  async function downloadAttachmentTemplate(){
    try{
      const response=await fetch(`${API_BASE_URL}/api/v1/migration-import/historical-attachments/template?start=${attachmentStart}&end=${attachmentEnd}`,{headers:apiHeaders(false)});
      if(!response.ok)throw new Error("Шаблон не получен: проверьте период, доступ и основной магазин.");
      const text=await response.text();setText(text);setMapping(Object.fromEntries(fields.historical_attachments.map(field=>[field,field])));change();
      const url=URL.createObjectURL(new Blob([text],{type:"text/csv;charset=utf-8"}));const anchor=document.createElement("a");anchor.href=url;anchor.download="historical-attachments.csv";anchor.click();URL.revokeObjectURL(url);
    }catch(error){setMessage(error instanceof Error?error.message:"Шаблон не получен");}
  }
  async function runPreview(){
    if(lock.current)return;lock.current=true;setBusy(true);setMessage("");setConsent(false);pending.current=null;
    const version=reviewEpoch.current;
    try{const result=await call<Preview>("/preview",{kind,csv_text:text,mapping});if(version===reviewEpoch.current)setPreview(result);}
    catch(error){setPreview(null);setMessage(error instanceof Error?error.message:"Ошибка предпросмотра");}
    finally{setBusy(false);lock.current=false;}
  }
  async function confirm(){
    if(lock.current||!canWrite||!consent||!preview?.can_confirm)return;
    pending.current??={kind,csv_text:text,mapping:{...mapping},request_key:crypto.randomUUID(),preview_fingerprint:preview.preview_fingerprint};
    lock.current=true;setBusy(true);setMessage("");
    try{const result=await call<{imported_count:number;replayed:boolean}>("/confirm",pending.current);pending.current=null;setPreview(null);setConsent(false);setMessage(`${result.replayed?"Импорт уже выполнен":"Импорт выполнен"}: ${result.imported_count} строк.`);await onRefresh().catch(()=>undefined);await loadHistory();}
    catch(error){
      const uncertain=error instanceof TypeError||error instanceof SyntaxError||(error instanceof Error&&/failed to fetch|network error|networkerror|timeout|unexpected end of json/i.test(error.message));
      setMessage(uncertain?"Результат импорта неизвестен: ответ сервера не получен. Повторите подтверждение без изменения таблицы — тот же запрос не создаст дубликат.":error instanceof Error?error.message:"Ошибка импорта");
    }
    finally{setBusy(false);lock.current=false;}
  }
  async function loadHistory(){try{setHistory((await call<{items:typeof history}>("/history")).items);}catch(error){setMessage(error instanceof Error?error.message:"История недоступна");}}
  async function readFile(file:File){
    change();setText("");setMapping({});const version=reviewEpoch.current;
    if(file.size>1_000_000){setMessage("Файл больше 1 МБ");return;}
    try{const value=await file.text();if(version===reviewEpoch.current)setText(value);}
    catch{if(version===reviewEpoch.current)setMessage("Не удалось прочитать файл CSV");}
  }
  return <section className="work-section migration-import" aria-labelledby="migration-title"><h2 id="migration-title">Перенос из таблиц: CSV</h2>
    <p>Только новые записи. Существующие товары, материалы и партии не заменяются. Сначала справочник материалов, затем начальные FIFO-остатки. Количество указывается в базовых единицах: м, кг, л, шт. Это локальный учёт, а не загрузка карточек на WB. Синхронизация WB может обновить импортированные названия товаров.</p>
    {kind==="finished_lots"||kind==="historical_documents"?<p role="note">Только основной магазин и незадействованные в общем складе артикулы. Остаток готовой партии вводится отдельно от исходного количества; история не списывает его повторно. Все документы каждой партии передаются одним полным файлом и должны сойтись по количеству и копейкам. Источник ручной: отчёт остаётся предварительным. Продажа: выручка и выплата положительные; возврат: отрицательные. Знак комиссий и прочих удержаний сохраняйте по исходному документу, не меняйте все расходы возврата на минус. Исторические данные WB не заменяются. Возврат после даты перехода по архивной продаже требует отдельного разбора.</p>:null}
    <fieldset disabled={busy}><legend>Исходная таблица и сопоставление столбцов</legend><label>Тип импорта<select value={kind} onChange={event=>{setKind(event.target.value as Kind);setMapping({});change();}}><option value="products">Товары</option><option value="materials">Материалы</option><option value="opening_materials">Начальные остатки сырья FIFO</option><option value="finished_lots">Готовые партии: остаток на дату перехода</option><option value="historical_attachments">Себестоимость: прикрепить документы к строкам WB</option><option value="historical_documents">Полная история партий: продажи и возвраты</option></select></label>
    {kind==="historical_attachments"?<section aria-label="Шаблон прикрепления себестоимости"><p>Прикрепление сохраняет исходные строки WB. Скачайте их версию за период, заполните только собственные документы, партии, распределения и связь возврата с продажей. Суммы и знаки сохраняйте точно по строке WB. Все документы каждой партии передаются одним полным файлом и должны сойтись с остатком на дату перехода. Только основной магазин и артикулы вне общего склада. Пустые или неизвестные данные не подтверждаются. Результат — ручной предварительный источник, не точный FIFO.</p><label>Начало периода<input type="date" value={attachmentStart} onChange={e=>setAttachmentStart(e.target.value)}/></label><label>Конец периода<input type="date" value={attachmentEnd} onChange={e=>setAttachmentEnd(e.target.value)}/></label><button type="button" onClick={()=>void downloadAttachmentTemplate()}>Скачать исходные строки WB для прикрепления</button></section>:null}
    <button type="button" onClick={template}>Заполнить пример шаблона</button><label>Файл CSV (UTF-8, до 1 МБ)<input type="file" accept=".csv,text/csv" onChange={event=>{const file=event.target.files?.[0];if(file)void readFile(file);}}/></label>
    <label>CSV: можно вставить текст<textarea value={text} maxLength={1_000_000} rows={7} onChange={event=>{setText(event.target.value);change();}}/></label>
    <div className="cashflow-controls">{fields[kind].map(field=><label key={field}>{labels[field]}<select value={mapping[field]??""} onChange={event=>{setMapping(current=>({...current,[field]:event.target.value}));change();}}><option value="">Выберите столбец</option>{columns.filter(Boolean).map((column,index)=><option key={`${column}-${index}`} value={column}>{column}</option>)}</select></label>)}</div>
    <button type="button" className="secondary-button" disabled={!text||fields[kind].some(field=>!mapping[field])} onClick={()=>void runPreview()}>Проверить без записи</button>
    </fieldset>{message?<p role="status">{message}</p>:null}{busy?<p role="status">Проверка / сохранение…</p>:null}
    {preview?<><h3>Предпросмотр: {preview.rows.length} строк</h3>{preview.errors.length?<ul aria-label="Ошибки импорта">{preview.errors.map((error,index)=><li key={index}>Строка {error.line}: {error.message}</li>)}</ul>:<p>Ошибок не найдено. До подтверждения ничего не записано.</p>}
    <div className="cashflow-table"><table><caption>Данные перед переносом (первые 30 строк)</caption><thead><tr><th>Строка</th>{fields[kind].map(field=><th key={field}>{labels[field]}</th>)}</tr></thead><tbody>{preview.rows.slice(0,30).map(row=><tr key={row.line}><td>{row.line}</td>{fields[kind].map(field=><td key={field}>{String(row.data[field])}</td>)}</tr>)}</tbody></table></div>
    {kind==="opening_materials"&&preview.rows.some(row=>conversionProof(row.data.unit_conversion))?<section aria-label="Перевод единиц перед переносом"><h4>Перевод в единицы справочника</h4><ul>{preview.rows.map(row=>{const proof=conversionProof(row.data.unit_conversion);return proof?<li key={row.line}>Строка {row.line}: {String(row.data.material_name)} — {proof.entered_quantity} {proof.entered_unit} × {proof.entered_unit_price_rub} ₽/{proof.entered_unit} → {proof.base_quantity} {proof.base_unit} × {proof.base_unit_price_rub} ₽/{proof.base_unit}. Сумма не меняется: {proof.source_total_rub} ₽.</li>:null;})}</ul></section>:null}
    {canWrite?<><label><input type="checkbox" checked={consent} disabled={busy||!preview.can_confirm} onChange={event=>setConsent(event.target.checked)}/>Проверил {preview.rows.length} строк: импортировать без замены существующих данных. Начальные остатки соответствуют моему подтверждённому учёту.</label><button type="button" className="primary-button" disabled={busy||!preview.can_confirm||!consent} onClick={()=>void confirm()}>Подтвердить импорт</button></>:<p>Подтверждать перенос может владелец или менеджер.</p>}</>:null}
    <details><summary onClick={()=>void loadHistory()}>Журнал импортов</summary><ul>{history.map(row=><li key={row.request_key}>{row.created_at}: {row.kind} · {row.imported_count} строк · {row.request_key}</li>)}</ul></details>
  </section>;
}
