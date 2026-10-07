import { lazy, useEffect, useRef, useState } from "react";
import { API_BASE_URL, apiHeaders } from "./data-source";
import { scopedFetch } from "./selected-shop";
import { previewMoney } from "./ozon-preview-money";
const OzonProductInsights=lazy(()=>import("./OzonProductInsights").then(module=>({default:module.OzonProductInsights})));
const OzonAdsAnalyticsPanel=lazy(()=>import("./OzonAdsAnalyticsPanel").then(module=>({default:module.OzonAdsAnalyticsPanel})));
const OzonMonthlyPanel=lazy(()=>import("./OzonMonthlyPanel").then(module=>({default:module.OzonMonthlyPanel})));
const OzonHistoricalPnl=lazy(()=>import("./OzonHistoricalPnl").then(module=>({default:module.OzonHistoricalPnl})));
const OzonNormalizationPanel=lazy(()=>import("./OzonNormalizationPanel").then(module=>({default:module.OzonNormalizationPanel})));
const OzonRealizationSource=lazy(()=>import("./OzonRealizationSource").then(module=>({default:module.OzonRealizationSource})));
import { CashflowPanel } from "./CashflowPanel";
import { CashAccountsPanel } from "./CashAccountsPanel";
import { ManufacturingCosts } from "./ManufacturingCosts";
const OzonPriceControls=lazy(()=>import("./OzonPriceControls").then(module=>({default:module.OzonPriceControls})));
import { WorkspaceTabs } from "./WorkspaceTabs";
import "./OzonCabinetViews.css";
import { LazyPanel, LazyDisclosure } from "./LazyPanel";

export type OzonCabinetPage = "today"|"stock"|"ads"|"finance"|"production"|"supply"|"agents"|"reports"|"products"|"control"|"settings";
type Props = {page: OzonCabinetPage;start:string;end:string;canWrite:boolean;isOwner:boolean;role:string};
type Row = Record<string,unknown>;
type Snapshot = {marketplace:string;loaded:boolean;synced_at:string|null;blockers:string[];items?:Row[];metrics?:Record<string,string|number|null>;truncated?:boolean;coverage?:{source_collection_complete:boolean;source_snapshot_stale:boolean;record_parse_complete:boolean;scope:string}};
async function request<T>(path:string,signal:AbortSignal,method="GET",body?:unknown):Promise<T>{
 const response=await scopedFetch(`${API_BASE_URL}/api/v1${path}`,{method,headers:apiHeaders(body!==undefined),signal,...(body===undefined?{}:{body:JSON.stringify(body)})});
 const result=await response.json();
 if(signal.aborted)throw new DOMException("Aborted","AbortError");
 if(!response.ok)throw new Error(typeof result.detail==="string"?result.detail:result.detail?.message??`Ozon: HTTP ${response.status}`);
 return result as T;
}
const text=(value:unknown)=>value==null?"Нет данных":String(value);
const metricLabels:Record<string,string>={order_units:"Единиц в отправлениях",document_revenue_rub:"Выручка по документам",document_profit_rub:"Результат по документам",unknown_cost_units:"Без подтверждённой стоимости",present_units:"Доступно на складах Ozon",reserved_units:"В резерве",posting_count:"Отправлений"};
const columns:Record<string,[string,string][]>= {
 stock:[["offer_id","Артикул продавца"],["sku","SKU Ozon"],["source","Источник"],["warehouse_id","Склад"],["present_units","Доступно"],["reserved_units","Резерв"]],
 orders:[["posting_number","Отправление"],["kind","Схема"],["status","Статус источника"],["created_at","Дата"],["quantity_units","Количество"]],
 returns:[["id","ID возврата"],["posting_number","Отправление"],["sku","SKU Ozon"],["quantity","Количество"],["status","Статус источника"],["created_at","Дата"]],
 control:[["label","Проверка"],["status","Состояние"],["note","Подробности"]],
};
function ReadModel({resource,start,end}:{resource:"overview"|"stock"|"orders"|"returns"|"control";start:string;end:string}){
 const [data,setData]=useState<Snapshot|null>(null),[error,setError]=useState(""),[refresh,setRefresh]=useState(0),[kind,setKind]=useState("all");
 useEffect(()=>{const controller=new AbortController();setData(null);setError("");
 void request<Snapshot>(`/ozon-cabinet/${resource}?start_date=${encodeURIComponent(start)}&end_date=${encodeURIComponent(end)}${resource==="orders"?`&kind=${kind}`:""}`,controller.signal).then(result=>{if(controller.signal.aborted)return;if(result.marketplace!=="ozon")throw new Error("Источник ответа не соответствует Ozon");setData(result);}).catch(e=>{if(!controller.signal.aborted)setError(e instanceof Error?e.message:String(e));});
 return()=>controller.abort();},[resource,start,end,refresh,kind]);
 return <section className="panel ozon-cabinet-panel" aria-label={`Данные Ozon: ${resource}`}>
 <div className="ozon-cabinet-toolbar">{resource==="orders"?<label>Схема отправлений<select value={kind} onChange={e=>setKind(e.target.value)}><option value="all">FBO и FBS</option><option value="fbo">FBO</option><option value="fbs">FBS</option></select></label>:null}<button type="button" className="secondary-button" onClick={()=>setRefresh(x=>x+1)}>Обновить сохранённые данные</button></div>
 {error?<p role="alert">{error}</p>:!data?<p role="status">Читаем сохранённые данные Ozon…</p>:<>
 <p role="status">{data.loaded?"Источник загружен":"Источник ещё не загружен"} · Обновлено: {data.synced_at?new Date(data.synced_at).toLocaleString("ru-RU"):"нет даты"}.</p>
 {data.coverage?.source_snapshot_stale?<p className="ozon-cabinet-warning">Снимок устарел; данные требуют повторной загрузки.</p>:null}
 <div className="ozon-cabinet-metrics">{Object.entries(data.metrics??{}).map(([key,value])=><article key={key}><span>{metricLabels[key]??key}</span><strong>{key.endsWith("_rub")?previewMoney(value==null?null:String(value)):text(value)}</strong></article>)}</div>
 {resource==="stock"||resource==="returns"?<p>Снимок текущего реестра; выбранный период не превращает его в исторические остатки или возвраты.</p>:null}
 {data.blockers?.length?<ul aria-label="Ограничения данных Ozon">{data.blockers.map((item,index)=><li key={index}>{item}</li>)}</ul>:null}
 {columns[resource]?<div className="ozon-cabinet-scroll"><table><thead><tr>{columns[resource].map(([key,label])=><th key={key}>{label}</th>)}</tr></thead><tbody>{(data.items??[]).map((row,index)=><tr key={index}>{columns[resource].map(([key])=><td key={key}>{text(row[key])}</td>)}</tr>)}</tbody></table>{!data.items?.length?<p>Строк в сохранённом источнике нет. Это не подтверждает нулевые продажи или расходы.</p>:null}</div>:null}
 {data.truncated?<p>Показана часть строк. Полноту источника проверяйте отдельно.</p>:null}
 </>}
 </section>;
}
function SellerSettings({start,end,canWrite,isOwner}:{start:string;end:string;canWrite:boolean;isOwner:boolean}){
 const [client,setClient]=useState(""),[key,setKey]=useState(""),[notice,setNotice]=useState(""),[busy,setBusy]=useState(false);
 const [workspace,setWorkspace]=useState<{configured?:boolean;loaded:boolean;products:Row[]}|null>(null),[drafts,setDrafts]=useState<Record<string,string>>({});
 const controller=useRef<AbortController|null>(null);
 useEffect(()=>{if(!isOwner||!canWrite){setClient("");setKey("");}},[isOwner,canWrite]);
 useEffect(()=>{if(!canWrite)setDrafts({});},[canWrite]);
 useEffect(()=>{const c=new AbortController();controller.current=c;setWorkspace(null);setDrafts({});setClient("");setKey("");setNotice("");setBusy(false);void request<typeof workspace>(`/ozon-accounting/workspace?start_date=${start}&end_date=${end}`,c.signal).then(setWorkspace).catch(e=>{if(!c.signal.aborted)setNotice(e.message);});return()=>c.abort();},[start,end]);
 const action=async(path:string,body:unknown,method:string)=>{if(busy||!canWrite||path==="/settings/ozon"&&!isOwner||!controller.current)return;setBusy(true);setNotice("");const signal=controller.current.signal;try{await request(path,signal,method,body);if(!signal.aborted){setKey("");setClient("");setNotice(path==="/settings/ozon"?"Сохранено защищённо. Обновите данные магазина для пересчёта.":"Операция завершена. Полноту загрузки проверьте в разделе «Контроль».");const next=await request<typeof workspace>(`/ozon-accounting/workspace?start_date=${start}&end_date=${end}`,signal);if(!signal.aborted)setWorkspace(next);}}catch(e){if(!signal.aborted)setNotice(e instanceof Error?e.message:String(e));}finally{if(!signal.aborted)setBusy(false);}};
 return <section className="panel ozon-cabinet-panel"><h2>Подключение Ozon Seller</h2><p>Ключ Seller используется для товаров, остатков и отправлений. Для рекламной аналитики нужен отдельный ключ Performance.</p><p>Подключение: {workspace?.configured===true?"настроено":workspace?.configured===false?"не настроено":"проверяем"}.</p>
 {isOwner&&canWrite?<form className="ozon-cabinet-form" onSubmit={e=>{e.preventDefault();void action("/settings/ozon",{client_id:client,api_key:key},"POST");}}><label>Client-Id Ozon<input required value={client} onChange={e=>setClient(e.target.value)} autoComplete="off" disabled={busy}/></label><label>Api-Key Ozon<input required type="password" value={key} onChange={e=>setKey(e.target.value)} autoComplete="new-password" disabled={busy}/></label><button className="primary-button" disabled={busy||!key.trim()||!client.trim()}>Сохранить ключ Seller</button></form>:<p>Ключ подключения может изменять только владелец.</p>}
 {canWrite?<button type="button" className="secondary-button" disabled={busy||workspace?.configured!==true} onClick={()=>void action("/ozon-accounting/sync",{start_date:start,end_date:end},"POST")}>Загрузить данные Seller за период</button>:null}
 {notice?<p role="status">{notice}</p>:null}<h3>Себестоимость SKU Ozon</h3><p>Ставка — ваша текущая оценка. Исторические партии и документы учитываются отдельно в «Финансах».</p>
 <div className="ozon-cabinet-scroll"><table><thead><tr><th>Артикул</th><th>SKU Ozon</th><th>Моя стоимость единицы, ₽</th><th>Действие</th></tr></thead><tbody>{workspace?.products?.slice(0,100).map((p,index)=>{const sku=String(p.sku??"");const mapping=p.cost_mapping as {unit_cost_text?:string|null}|undefined;return <tr key={index}><td>{text(p.offer_id)}</td><td>{sku||"Нет SKU"}</td><td><input aria-label={`Себестоимость SKU ${sku}`} inputMode="decimal" value={drafts[sku]??mapping?.unit_cost_text??""} disabled={!canWrite||busy} onChange={e=>setDrafts({...drafts,[sku]:e.target.value})}/></td><td><button className="secondary-button" disabled={!canWrite||busy||!sku||drafts[sku]===undefined||!drafts[sku].trim()} onClick={()=>void action("/ozon-accounting/cost",{sku,mode:"manual",unit_cost:drafts[sku].replace(",",".")},"PUT")}>Сохранить стоимость</button></td></tr>;})}</tbody></table></div>{(workspace?.products?.length??0)>100?<p>Первые 100 товаров; другие SKU доступны в документальном учёте.</p>:null}
 </section>;
}
function OzonAccess(){
 const [items,setItems]=useState<{account:string;can_write:boolean;active:boolean}[]>([]),[account,setAccount]=useState(""),[write,setWrite]=useState(false),[active,setActive]=useState(true),[error,setError]=useState(""),[busy,setBusy]=useState(false),[notice,setNotice]=useState("");
 const controller=useRef<AbortController|null>(null);
 useEffect(()=>{const c=new AbortController();controller.current=c;void request<{items:typeof items}>("/marketplace-context/ozon/access",c.signal).then(d=>setItems(d.items)).catch(e=>{if(!c.signal.aborted)setError(e.message);});return()=>c.abort();},[]);
 const save=async()=>{if(busy||!account.trim()||!controller.current)return;const signal=controller.current.signal;setBusy(true);setError("");setNotice("");try{await request("/marketplace-context/ozon/access",signal,"PUT",{account:account.trim(),can_write:write,active});const next=await request<{items:typeof items}>("/marketplace-context/ozon/access",signal);if(!signal.aborted){setItems(next.items);setAccount("");setNotice("Доступ Ozon сохранён.");}}catch(e){if(!signal.aborted)setError(e instanceof Error?e.message:String(e));}finally{if(!signal.aborted)setBusy(false);}};
 return <section className="panel ozon-cabinet-panel"><h2>Доступ команды к Ozon</h2><p>Доступ к WB не открывает Ozon автоматически. Запись доступна только при соответствующей роли кабинета и отдельном разрешении.</p>{error?<p role="alert">{error}</p>:null}{notice?<p role="status">{notice}</p>:null}<div className="ozon-cabinet-scroll"><table><thead><tr><th>Аккаунт</th><th>Доступ</th><th>Запись</th></tr></thead><tbody>{items.map(i=><tr key={i.account}><td>{i.account}</td><td>{i.active?"Включён":"Отключён"}</td><td>{i.can_write?"Разрешена по роли":"Только просмотр"}</td></tr>)}</tbody></table></div><form className="ozon-cabinet-form" onSubmit={e=>{e.preventDefault();void save();}}><label>Аккаунт участника<input required value={account} disabled={busy} onChange={e=>setAccount(e.target.value)}/></label><label><input type="checkbox" checked={active} disabled={busy} onChange={e=>setActive(e.target.checked)}/>Доступ к Ozon включён</label><label><input type="checkbox" checked={write} disabled={busy} onChange={e=>setWrite(e.target.checked)}/>Разрешить запись по роли</label><button className="secondary-button" disabled={busy||!account.trim()}>Сохранить доступ Ozon</button></form></section>;
}
export function OzonCabinetViews(props:Props){
 const {page,start,end}=props;
 const canWrite=props.canWrite&&['owner','manager'].includes(props.role);
 const isOwner=props.isOwner&&props.role==='owner';
 const readonly=<p>Показываем данные Ozon и явно отмечаем неполноту источников. Неизвестная себестоимость не считается нулевой.</p>;
 if(page==="products")return <div className="ozon-cabinet-views"><WorkspaceTabs ariaLabel="Товары Ozon" initial="ozon-products" tabs={[{id:"ozon-products",label:"Экономика и план"},{id:"ozon-prices",label:"Цены Ozon"}]} panels={{"ozon-products":<LazyPanel label="экономику товаров Ozon"><OzonProductInsights {...{start,end,canWrite}}/></LazyPanel>,"ozon-prices":<LazyPanel label="цены Ozon"><OzonPriceControls canWrite={canWrite}/></LazyPanel>}}/></div>;
 if(page==="ads")return <div className="ozon-cabinet-views"><LazyPanel label="аналитику рекламы Ozon"><OzonAdsAnalyticsPanel {...{start,end,canWrite,isOwner}}/></LazyPanel></div>;
 if(page==="finance")return <div className="ozon-cabinet-views">{readonly}<LazyPanel label="ОПиУ Ozon"><OzonMonthlyPanel {...{start,end,canWrite}}/></LazyPanel><LazyDisclosure title="Сверка начислений API и документального реестра"><OzonNormalizationPanel {...{start,end,canWrite}}/></LazyDisclosure><LazyDisclosure title="Документальные партии и исторический FIFO"><OzonHistoricalPnl {...{start,end,canWrite}}/></LazyDisclosure><LazyDisclosure title="Документы реализации Ozon"><OzonRealizationSource canWrite={canWrite}/></LazyDisclosure><details className="panel ozon-cabinet-panel"><summary>Общие счета и движение денег кабинета</summary><p>Счета общие для WB и Ozon. Это внесённые денежные операции, а не автоматические выплаты Ozon.</p><CashflowPanel role={canWrite?props.role:"viewer"}/><CashAccountsPanel role={canWrite?props.role:"viewer"} start={start} end={end}/></details></div>;
 if(page==="reports")return <div className="ozon-cabinet-views"><h2>Отчёты Ozon</h2><p>Помесячный ОПиУ по документам и выгрузка CSV. Начисления, выручка и выплаты — разные показатели.</p><LazyPanel label="ОПиУ Ozon"><OzonMonthlyPanel {...{start,end,canWrite:false}}/></LazyPanel></div>;
 if(page==="settings")return <div className="ozon-cabinet-views"><SellerSettings {...{start,end,canWrite,isOwner}}/>{isOwner&&canWrite?<OzonAccess/>:null}<p>Подключение рекламного Performance API доступно в разделе «Реклама».</p></div>;
 if(page==="production")return <div className="ozon-cabinet-views"><h2>Общее производство кабинета</h2><p>Сырьё, производственные партии и физический склад общие для кабинета. Физический каталог использует идентификаторы WB; SKU Ozon автоматически не сопоставляются. Документальные партии Ozon учитываются отдельно в «Финансах».</p><ManufacturingCosts canEdit={canWrite}/></div>;
 if(page==="agents")return <div className="ozon-cabinet-views"><h2>Автоматизация Ozon</h2><p>Автоматическое управление рекламным бюджетом и ставками Ozon пока не подключено. Аналитика Performance работает отдельно в «Рекламе».</p><p>Работа с ценами выполняется вручную через проверенный черновик и отдельное подтверждение. Автоматических правил изменения цен здесь нет.</p><LazyPanel label="черновики цен Ozon"><OzonPriceControls canWrite={canWrite} mode="drafts"/></LazyPanel></div>;
 return <div className="ozon-cabinet-views">{page==="today"?readonly:null}<ReadModel key={`${page}:${start}:${end}`} resource={page==="today"?"overview":page==="supply"?"orders":page==="stock"?"stock":"control"} {...{start,end}}/>{page==="supply"?<><h2>Возвраты Ozon</h2><ReadModel resource="returns" {...{start,end}}/><p>Здесь просмотр сохранённых отправлений и возвратов. Изменение статусов, создание поставки и возврат денег не выполняются.</p></>:null}</div>;
}
