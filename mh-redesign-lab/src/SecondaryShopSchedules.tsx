import {useEffect,useRef,useState} from 'react';
import {API_BASE_URL} from './data-source';
import {currentShop,downloadScoped} from './selected-shop';
import {getShopSchedules,scheduleRequest,scheduleAction,ScheduleSubmission,type ScheduleInput,type ScheduleListing,type ShopSchedule,type ScheduleKind} from './shop-schedules';

const names:Record<string,string>={pnl:'Прибыль по учтённым данным',sales_orders:'Продажи и заказы',ads:'Реклама',stocks:'Остатки',unit_economics:'Экономика товаров',cost_structure:'Структура расходов',abc_analysis:'Вклад товаров в продажи',buyout_rate:'Доля выкупа',oos_risk:'Риск отсутствия товара',wb_deductions:'Удержания WB',ad_performance:'Эффективность рекламы',warehouse_stocks:'Остатки по складам',returns_cancellations:'Возвраты и отмены',organic_vs_ads:'Продажи с рекламой и без',ad_funnel:'Воронка рекламы',wb_commission_rate:'Комиссия WB',stock_in_transit:'Товары в пути',weekday_pattern:'Продажи по дням недели',wb_incident_losses:'Потери и расхождения WB',frozen_capital:'Деньги в остатках',roi_by_sku:'Отдача затрат по товарам'};
function utc(value:string|null|undefined){if(!value)return 'Не назначено';const parsed=new Date(value);return Number.isFinite(parsed.getTime())?parsed.toLocaleString('ru-RU',{timeZone:'UTC'})+' UTC':'Дата недоступна';}
function status(value:string|null|undefined){return value==='ok'?'Выполнено':value==='failed'?'Не выполнено':value?'Статус требует проверки':'Ещё не запускалось';}
const failureNames:Record<string,string>={membership_denied:'Права участника больше не разрешают выполнение',snapshot_conflict:'Версия магазина изменилась во время задания',sync_incomplete:'Обновление WB не завершено; прежние данные сохранены',membership_expired:'Не удалось повторно подтвердить действующие права участника',credential_changed:'Изменилась учётная запись продавца',run_failed:'Задание не завершено; проверьте подключение магазина'};
function failure(value:string|null|undefined){return value?(failureNames[value]??'Задание не завершено. Проверьте подключение, права и версию магазина.') :'';}
export function SecondaryShopSchedules(){
 const shop=currentShop(),[listing,setListing]=useState<ScheduleListing|null>(null),[error,setError]=useState(''),[loading,setLoading]=useState(true),[download,setDownload]=useState<string|null>(null);
 const prefix=shop?.id??0;
 async function refresh(){setListing(await getShopSchedules());}
 useEffect(()=>{if(prefix<=0){setLoading(false);return;}const abort=new AbortController();getShopSchedules(abort.signal).then(setListing).catch(caught=>{if(!abort.signal.aborted)setError(caught instanceof Error?caught.message:'Расписания недоступны');}).finally(()=>{if(!abort.signal.aborted)setLoading(false);});return()=>abort.abort();},[prefix]);
 if(prefix<=0)return null;
 const canConfigure=Boolean(listing?.can_configure&&shop?.allowed_actions.includes(scheduleAction));
 async function downloadRun(id:string){if(download)return;setDownload(id);setError('');try{await downloadScoped(`${API_BASE_URL}/api/v1/operations/reports/runs/${encodeURIComponent(id)}/download`,`отчёт-${id}.pdf`);}catch(caught){setError(caught instanceof Error?caught.message:'PDF недоступен');}finally{setDownload(null);}}
 return <section className="work-section" aria-labelledby="secondary-schedules"><h2 id="secondary-schedules">Расписания · {shop?.name}</h2><p>Автоматическое обновление и локальные PDF включаются отдельно. По умолчанию оба расписания выключены. Отчёт сохраняется в этом магазине; отправка по почте и в мессенджеры не настроена.</p>
 {loading&&<p role="status">Загружаем расписания…</p>}{error&&<p role="alert">{error}</p>}
 {listing&&!canConfigure&&<p>Только просмотр. Настраивать и отключать расписания может владелец. Для включения также нужно право записи в этот магазин.</p>}
 {listing&&!listing.can_enable&&<p>Для включения нужно действующее право записи в магазин. Владелец может отключить ранее созданное расписание.</p>}
 {listing?.schedules.map(schedule=><ScheduleForm key={`${prefix}-${schedule.kind}-${schedule.generation}`} schedule={schedule} metrics={listing.metrics} canConfigure={canConfigure} canEnable={listing.can_enable} refresh={refresh}/>)}
 {listing&&<><h3>Последние попытки</h3>{!listing.runs.length?<p>Задания ещё не запускались.</p>:<table><thead><tr><th>Задание</th><th>Назначено</th><th>Результат</th><th>Локальный PDF</th></tr></thead><tbody>{listing.runs.map(run=><tr key={run.id}><td>{run.kind==='sync'?'Обновление WB':'PDF'}</td><td>{utc(run.due_utc)}</td><td>{status(run.status)}{run.error_code&&<p>{failure(run.error_code)}</p>}</td><td>{run.status==='ok'&&run.artifact?<button type="button" className="secondary-button" disabled={download!==null} onClick={()=>void downloadRun(String(run.artifact!.run_id))}>{download===String(run.artifact.run_id)?'Загружаем…':'Скачать PDF'}</button>:'Не создан'}</td></tr>)}</tbody></table>}</>}
 </section>;
}
function ScheduleForm({schedule,metrics,canConfigure,canEnable,refresh}:{schedule:ShopSchedule;metrics:string[];canConfigure:boolean;canEnable:boolean;refresh:()=>Promise<void>}){
 const [enabled,setEnabled]=useState(schedule.enabled),[interval,setInterval]=useState(String(schedule.interval_minutes)),[period,setPeriod]=useState(String(schedule.period_days)),[selected,setSelected]=useState(schedule.metrics),[busy,setBusy]=useState(false),[error,setError]=useState(''),[message,setMessage]=useState('');
 const receipt=useRef(new ScheduleSubmission()),kind:ScheduleKind=schedule.kind,name=kind==='sync'?'Обновление WB':'Локальный PDF';
 async function save(){if(!canConfigure||busy)return;setBusy(true);setError('');setMessage('');try{
  if(enabled&&!canEnable)throw new Error('Для включения нужно действующее право записи в магазин. Можно отключить расписание.');
  const cadence=Number(interval),days=Number(period),minimum=kind==='sync'?15:60,maximum=kind==='sync'?1440:10080;
  if(!interval.trim()||!Number.isInteger(cadence)||cadence<minimum||cadence>maximum||!period.trim()||!Number.isInteger(days)||days<1||days>90)throw new Error(`Укажите интервал ${minimum}–${maximum} минут и период 1–90 дней`);
  if(kind==='pdf'&&(!selected.length||selected.length>8||selected.some(metric=>!metrics.includes(metric))))throw new Error('Выберите от одной до восьми метрик PDF');
  const body:ScheduleInput={enabled,expected_generation:schedule.generation,interval_minutes:cadence,period_days:days,metrics:kind==='sync'?[]:[...selected].sort()},pending=receipt.current.snapshot(kind,body);
  await scheduleRequest<ShopSchedule>(kind,pending.input,pending.key);
  // Preserve the receipt until fresh state is received; a lost refresh may be retried safely.
  await refresh();receipt.current.clear();setMessage('Настройки расписания сохранены.');
 }catch(caught){setError(caught instanceof Error?caught.message:'Настройки не сохранены');}finally{setBusy(false);}}
 return <form className="panel" aria-label={`Расписание ${name}`} onSubmit={event=>{event.preventDefault();void save();}}><h3>{name}</h3><p>{schedule.enabled?'Включено':'Выключено'} · версия {schedule.generation}</p><label><input type="checkbox" aria-label={`Включить ${name}`} checked={enabled} disabled={!canConfigure||busy||(!canEnable&&!enabled)} onChange={event=>{if(!event.target.checked||canEnable)setEnabled(event.target.checked);}}/> Включить {name}</label>
 <label className="operation-field"><span>Интервал {name}, минут</span><input aria-label={`Интервал ${name}, минут`} inputMode="numeric" value={interval} disabled={!canConfigure||busy} onChange={event=>setInterval(event.target.value)}/></label>
 <label className="operation-field"><span>Период {name}, дней</span><input aria-label={`Период ${name}, дней`} inputMode="numeric" value={period} disabled={!canConfigure||busy} onChange={event=>setPeriod(event.target.value)}/></label>
 {kind==='pdf'&&<fieldset disabled={!canConfigure||busy}><legend>Метрики PDF (до восьми)</legend>{metrics.map(metric=><label key={metric}><input type="checkbox" disabled={!names[metric]} checked={selected.includes(metric)} onChange={event=>setSelected(values=>event.target.checked?[...values,metric]:values.filter(value=>value!==metric))}/>{names[metric]??'Неизвестная метрика — недоступна'}</label>)}</fieldset>}
 <p>{schedule.enabled?`Следующая попытка: ${utc(schedule.next_due_utc)}`:'Расписание выключено — следующая попытка не назначена.'}</p><p>Последняя попытка: {status(schedule.last_status)} {failure(schedule.last_error)}</p>
 {error&&<p role="alert">{error} После перезагрузки проверьте сохранённое расписание перед новым действием.</p>}{message&&<p role="status">{message}</p>}
 <button type="submit" className="secondary-button" disabled={!canConfigure||busy}>{busy?'Сохраняем…':`Сохранить ${name}`}</button></form>;
}
