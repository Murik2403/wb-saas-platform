import { useEffect, useRef, useState } from "react";
import { scopedFetch as fetch } from "./selected-shop";
import { API_BASE_URL, apiHeaders } from "./data-source";
import { previewMoney } from "./ozon-preview-money";
type Branch = { quantity_text: string | null; total_rub: string | null };
type Source = { loaded: boolean; document_number?: string; document_date?: string; source_version?: string; source_row_count?: number; period?:{start_date:string;end_date:string}; partial_period?:boolean; current_month_provisional?: boolean; display_truncated?: boolean; rows: Array<{source_occurrence_key:string; row_number_text:string|null; sku_text:string|null; posting_number:string|null; delivery_commission:Branch; return_commission:Branch; reason:string|null}> };
const text = (value:unknown) => typeof value === "string" ? value : "не указано / не распознано";
export function OzonRealizationSource({canWrite}:{canWrite:boolean}) {
  const now=new Date(); const [month,setMonth]=useState(`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}`);
  const [data,setData]=useState<Source|null>(null),[busy,setBusy]=useState(false),[error,setError]=useState("");
  const generation=useRef(0);
  const read=async(load=false)=>{
    const current=++generation.current; setBusy(true);setError("");
    const [year,value]=month.split("-").map(Number);
    try {
      const response=await fetch(`${API_BASE_URL}/api/v1/ozon-accounting/realization${load?"/load":`?month=${value}&year=${year}`}`,{method:load?"POST":"GET",headers:apiHeaders(load),...(load?{body:JSON.stringify({month:value,year})}:{})});
      if(!response.ok){const e=await response.json().catch(()=>({}));throw new Error(typeof e.detail==="string"?e.detail:`Ozon: HTTP ${response.status}`);}
      const result=await response.json() as Source;if(current===generation.current)setData(result);
    } catch(e){if(current===generation.current)setError(e instanceof Error?e.message:"Отчёт не загружен");}
    finally{if(current===generation.current)setBusy(false);}
  };
  useEffect(()=>{setData(null);void read();return()=>{++generation.current;};},[month]);
  return <section aria-labelledby="ozon-realization-title"><h3 id="ozon-realization-title">Месячный документ Ozon: доставка и возвраты</h3>
    <p>Отдельный источник по доставленным и возвращённым товарам. Отмены и невыкупы не входят; для Казахстана отчёт недоступен. Суммы этого документа не прибавляются к дневным начислениям Ozon.</p>
    <div className="ozon-controls"><label>Месяц документа Ozon<input type="month" min="2023-08" max={`${now.getFullYear()}-${String(now.getMonth()+1).padStart(2,"0")}`} value={month} disabled={busy} onChange={e=>setMonth(e.target.value)}/></label><button type="button" className="secondary-button" disabled={busy||!month} onClick={()=>void read()}>Открыть сохранённый документ</button><button type="button" className="secondary-button" disabled={busy||!canWrite||!month} onClick={()=>void read(true)}>Загрузить месячный документ Ozon</button></div>
    {error?<p role="alert">{error} Предыдущий сохранённый документ не заменён.</p>:null}
    {data?.loaded?<><p>Документ: {text(data.document_number)} · {text(data.document_date)}. Строк: {data.source_row_count}. Версия источника: {text(data.source_version)}.</p>
      <p>Период исходного документа: {text(data.period?.start_date)} — {text(data.period?.end_date)}.</p>
      {data.partial_period?<p>Документ охватывает часть выбранного месяца. Полнота продаж и возвратов за месяц не подтверждена.</p>:null}
      {data.source_row_count===0?<p>Источник не вернул строк документа. Это не подтверждение нулевых продаж или прибыли.</p>:null}
      {data.current_month_provisional?<p>Текущий месяц: документ предварительный и может измениться.</p>:null}
      <p>Количество и итог каждой секции показаны со знаком источника. Правила исправлений и связь возврата с исходной продажей не подтверждены. Это не денежные проводки и не списания FIFO; прибыль не рассчитана.</p>
      <div className="ozon-table-wrap" tabIndex={0} role="region" aria-label="Источник реализации Ozon"><table><thead><tr><th>Строка документа / отправление</th><th>SKU</th><th>Доставка: количество</th><th>Доставка: итог RUB</th><th>Возврат: количество</th><th>Возврат: итог RUB</th><th>Замечание</th></tr></thead><tbody>{(Array.isArray(data.rows)?data.rows:[]).map(row=><tr key={row.source_occurrence_key}><td>{text(row.row_number_text)}<small>{text(row.posting_number)}</small></td><td>{text(row.sku_text)}</td><td>{text(row.delivery_commission?.quantity_text)}</td><td>{previewMoney(row.delivery_commission?.total_rub)}</td><td>{text(row.return_commission?.quantity_text)}</td><td>{previewMoney(row.return_commission?.total_rub)}</td><td>{typeof row.reason==="string"?row.reason:"—"}</td></tr>)}</tbody></table></div>
      {data.display_truncated?<p>Показаны первые 100 строк; весь ответ сохранён без удаления повторных вхождений.</p>:null}
    </>:data?<p>Сохранённого месячного документа нет. Отсутствие отчёта не означает нулевые продажи или возвраты.</p>:<p>Читаем сохранённый месячный документ…</p>}
  </section>;
}
