import { useEffect, useRef, useState } from 'react';
import { API_BASE_URL, apiHeaders } from './data-source';
import { scopedFetch } from './selected-shop';
import { previewMoney } from './ozon-preview-money';
import { buildCsv } from './csv';
import './OzonProductInsights.css';

type View = 'products' | 'heatmap' | 'rnp';
type Metric = 'order_units' | 'document_sales_units' | 'document_revenue_rub';
type Product = {sku: string; offer_id?: string; order_units: number|null; sales_units: number|null; return_units: number|null;
  revenue_rub: string|null; historical_cogs_rub: string|null; result_after_fifo_rub: string|null;
  unknown_cost_units: number|null; plan_units?: number|null; fact_units?: number|null; favorite?: boolean;
  plan_days?: Array<{day:string;units:number}>; completion_pct?:string|null};
type Report = {items: Product[]; blockers: string[]; loaded: boolean; truncated: boolean;
  coverage: {source_collection_complete:boolean;marketplace_complete:boolean}; unallocated_document_fees_rub?:string|null};
type Heatmap = {dates:string[];items:Array<{sku:string;values:Array<string|number|null>}>;metric:Metric;
  basis:string;blockers:string[];metric_note:string;truncated:boolean};
const metrics: Array<[Metric,string]> = [['order_units','Созданные заказы, шт.'],['document_sales_units','Продажи − возвраты по документам, шт.'],['document_revenue_rub','Выручка по документам, ₽']];
const count = (value:number|null|undefined) => value==null?'—':value.toLocaleString('ru-RU');

async function request<T>(path:string,signal:AbortSignal,body?:unknown):Promise<T> {
  const response = await scopedFetch(API_BASE_URL+'/api/v1/ozon-insights/'+path,
    {method:body===undefined?'GET':'PUT',headers:apiHeaders(body!==undefined),signal,
      body:body===undefined?undefined:JSON.stringify(body)});
  const result = await response.json();
  if(!response.ok) throw new Error(typeof result.detail==='string'?result.detail:'Не удалось загрузить данные Ozon.');
  return result as T;
}

function download(report:Report) {
  const csv=buildCsv(['SKU Ozon','Артикул продавца','Заказы, шт.','Продажи документов, шт.','Возвраты документов, шт.','Выручка документов, ₽','FIFO документов, ₽','Результат до нераспределённых сборов, ₽'],
    report.items.map(r=>[r.sku,r.offer_id??'',r.order_units,r.sales_units,r.return_units,r.revenue_rub,r.historical_cogs_rub,r.result_after_fifo_rub]));
  const url=URL.createObjectURL(new Blob([csv],{type:'text/csv;charset=utf-8'}));
  const link=document.createElement('a');link.href=url;link.download='ozon-products.csv';link.click();URL.revokeObjectURL(url);
}

export function OzonProductInsights({start,end,canWrite}:{start:string;end:string;canWrite:boolean}) {
  const [view,setView]=useState<View>('products');
  const [metric,setMetric]=useState<Metric>('order_units');
  const [namespace,setNamespace]=useState('main');
  const [namespaceDraft,setNamespaceDraft]=useState('main');
  const [report,setReport]=useState<Report|null>(null);
  const [heatmap,setHeatmap]=useState<Heatmap|null>(null);
  const [loading,setLoading]=useState(false);
  const [error,setError]=useState('');
  const [status,setStatus]=useState('');
  const [revision,setRevision]=useState(0);
  const [day,setDay]=useState(end);
  const [drafts,setDrafts]=useState<Record<string,string>>({});
  const [busy,setBusy]=useState(false);
  const writeController=useRef<AbortController|null>(null);
  const context=useRef(0);
  const contextIdentity=useRef('');
  useEffect(()=>{setDay(end);setDrafts({});},[start,end,namespace]);
  useEffect(()=>{
    const controller=new AbortController();let active=true;context.current++;
    const identity=[start,end,view,metric,namespace].join('|');
    if(contextIdentity.current!==identity){setStatus('');contextIdentity.current=identity;}
    writeController.current?.abort();setBusy(false);setError('');setReport(null);setHeatmap(null);setLoading(true);
    const query=new URLSearchParams({start_date:start,end_date:end,namespace});
    const load=view==='heatmap'?request<Heatmap>('heatmap?'+query+'&metric='+metric,controller.signal)
      :request<Report>(view+'?'+query,controller.signal);
    load.then(data=>{if(active){if(view==='heatmap')setHeatmap(data as Heatmap);else setReport(data as Report);setLoading(false);}},
      reason=>{if(active){setError(reason instanceof Error?reason.message:'Ошибка Ozon');setLoading(false);}});
    return()=>{active=false;controller.abort();writeController.current?.abort();context.current++;};
  },[start,end,view,metric,namespace,revision]);

  async function write(path:string,body:unknown) {
    if(!canWrite||busy)return;
    const generation=context.current,controller=new AbortController();writeController.current=controller;setBusy(true);setStatus('');setError('');
    try {
      await request(path,controller.signal,body);
      if(generation===context.current&&!controller.signal.aborted){setRevision(v=>v+1);setStatus('✓ Сохранено');}
    } catch(reason) {if(generation===context.current&&!controller.signal.aborted)setError(reason instanceof Error?reason.message:'Ошибка сохранения');}
    finally {if(generation===context.current)setBusy(false);}
  }

  function plan(row:Product) {
    const saved=row.plan_days?.find(p=>p.day===day)?.units;
    const draft=drafts[row.sku]??(saved==null?'':String(saved));
    if(draft!==''&&(!/^\d+$/.test(draft)||Number(draft)>1000000)){setError('План: целое число от 0 до 1 000 000.');return;}
    void write('rnp/plan',{namespace,sku:row.sku,day,units:draft===''?null:Number(draft)});
  }
  const blockers=report?.blockers??heatmap?.blockers??[];
  return <section className="work-section ozon-product-insights" aria-label="Товарная аналитика Ozon">
    <h3>Товарная аналитика Ozon</h3>
    <nav aria-label="Вид товарной аналитики Ozon" className="opi-tabs">
      {([['products','Товары'],['heatmap','Тепловая карта'],['rnp','План и факт']] as const).map(([id,label])=><button type="button" key={id} aria-pressed={view===id} onClick={()=>setView(id)}>{label}</button>)}
    </nav>
    <label>Документальный реестр <input aria-label="Реестр аналитики Ozon" value={namespaceDraft} maxLength={80} onChange={event=>setNamespaceDraft(event.target.value)}/><button type="button" onClick={()=>setNamespace(namespaceDraft.trim()||'main')}>Применить реестр</button></label>
    <p className="opi-note">Заказы — созданные отправления, включая отменённые; они не заменяют продажи. Выручка и FIFO взяты только из внесённых документов Ozon. Пустое значение означает отсутствие подтверждения.</p>
    {loading&&<p role="status">Загрузка аналитики Ozon…</p>}
    {error&&<p role="alert">{error}</p>}
    {status&&<p role="status">{status}</p>}
    {blockers.length>0&&<ul className="opi-blockers">{blockers.map(value=><li key={value}>{value}</li>)}</ul>}
    {view==='products'&&report&&<>
      <button type="button" onClick={()=>download(report)} disabled={!report.items.length}>Скачать таблицу CSV</button>
      <p className="opi-note">Нераспределённые сборы документов: {previewMoney(report.unallocated_document_fees_rub)}. Они включены в общий месячный результат и не распределены по SKU.</p>
      {report.items.length?<div className="opi-scroll" role="region" aria-label="Таблица товаров Ozon" tabIndex={0}><table><thead><tr>{['SKU Ozon','Артикул','Заказы','Продажи документов','Возвраты','Выручка документов','FIFO документов','Результат SKU до общих сборов'].map(h=><th key={h}>{h}</th>)}</tr></thead>
        <tbody>{report.items.map(row=><tr key={row.sku}><th scope="row">{row.sku}</th><td>{row.offer_id||'—'}</td><td>{count(row.order_units)}</td><td>{count(row.sales_units)}</td><td>{count(row.return_units)}</td><td>{previewMoney(row.revenue_rub)}</td><td>{previewMoney(row.historical_cogs_rub)}</td><td>{previewMoney(row.result_after_fifo_rub)}</td></tr>)}</tbody></table></div>:<p>Нет подтверждённых товаров за выбранный период. Загрузите API-источник или внесите документы Ozon.</p>}
    </>}
    {view==='heatmap'&&<>
      <label>Метрика <select aria-label="Метрика тепловой карты Ozon" value={metric} onChange={event=>setMetric(event.target.value as Metric)}>{metrics.map(([id,label])=><option value={id} key={id}>{label}</option>)}</select></label>
      {heatmap&&<><p className="opi-note">{heatmap.metric_note} Даты заказов — UTC, документальных операций — Москва.</p>
        {heatmap.items.length?<div className="opi-scroll" role="region" aria-label="Тепловая карта Ozon" tabIndex={0}><table><thead><tr><th>SKU Ozon</th>{heatmap.dates.map(d=><th key={d}>{d.slice(5)}</th>)}</tr></thead>
          <tbody>{heatmap.items.map(row=><tr key={row.sku}><th scope="row">{row.sku}</th>{row.values.map((value,i)=><td key={heatmap.dates[i]} className={value==null?'opi-cell-unknown':Number(value)===0?'':'opi-cell-value'} title={value==null?'Нет подтверждения':String(value)}>{value==null?'—':metric==='document_revenue_rub'?previewMoney(String(value)):count(Number(value))}</td>)}</tr>)}</tbody></table></div>:<p>Для тепловой карты пока нет данных Ozon.</p>}</>}
    </>}
    {view==='rnp'&&report&&<>
      <label>Дата плана <input aria-label="Дата плана Ozon" type="date" min={start} max={end} value={day} onChange={event=>{setDay(event.target.value);setDrafts({});}}/></label>
      <p className="opi-note">Факт — продажи минус возвраты по документам. Прогноз не подставляется при неполных продажах. Пустой план можно сохранить для удаления записи.</p>
      {!canWrite&&<p>Изменения плана доступны владельцу и менеджеру.</p>}
      {report.items.length?<div className="opi-scroll" role="region" aria-label="План и факт Ozon" tabIndex={0}><table><thead><tr>{['SKU Ozon','Избранное','План периода','Факт документов','Выполнение','План на дату','Действие'].map(h=><th key={h}>{h}</th>)}</tr></thead>
        <tbody>{report.items.map(row=><tr key={row.sku}><th scope="row">{row.sku}</th><td><button type="button" aria-label={`Избранное SKU ${row.sku}`} aria-pressed={Boolean(row.favorite)} disabled={!canWrite||busy} onClick={()=>void write('rnp/favorite',{namespace,sku:row.sku,favorite:!row.favorite})}>{row.favorite?'★':'☆'}</button></td><td>{count(row.plan_units)}</td><td>{count(row.fact_units)}</td><td>{row.completion_pct==null?'—':row.completion_pct+'%'}</td><td><input aria-label={`План SKU ${row.sku}`} inputMode="numeric" value={drafts[row.sku]??String(row.plan_days?.find(p=>p.day===day)?.units??'')} disabled={!canWrite||busy} onChange={event=>setDrafts(d=>({...d,[row.sku]:event.target.value}))}/></td><td><button type="button" disabled={!canWrite||busy||!day} onClick={()=>plan(row)}>Сохранить</button></td></tr>)}</tbody></table></div>:<p>Загрузите каталог Ozon или документальные продажи, чтобы выбрать SKU для плана.</p>}
    </>}
    {(report?.truncated||heatmap?.truncated)&&<p className="opi-note">Показан ограниченный набор SKU для безопасного размера отчёта.</p>}
  </section>;
}
