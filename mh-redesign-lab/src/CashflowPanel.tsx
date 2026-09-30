import { CashAccountsPanel } from "./CashAccountsPanel";
import { useEffect, useRef, useState, type FormEvent } from "react";
import { changeCashEntry, createCashEntry, getCashflow, type CashReport } from "./cashflow-source";
import "./CashflowPanel.css";
const today = () => new Date().toLocaleDateString("en-CA");
const money = (minor: number) => `${new Intl.NumberFormat("ru-RU", {minimumFractionDigits:2, maximumFractionDigits:2}).format(minor/100)} ₽`;
const categories: Record<string, string> = {marketplace_payout:"Выплата маркетплейса",procurement:"Оплата закупки",salary:"Зарплата",tax:"Налог",rent:"Аренда",advertising:"Реклама",other:"Другое",opening:"Начальный остаток"};
const statuses: Record<string,string> = {planned:"Запланировано",actual:"Фактически",cancelled:"Отменено"};

export function CashflowPanel({role, compact = false}: {role: string; compact?: boolean}) {
  const canWrite = role === "owner" || role === "manager";
  const [start,setStart] = useState(() => `${today().slice(0,7)}-01`);
  const [end,setEnd] = useState(today);
  const [report,setReport] = useState<CashReport | null>(null);
  const [error,setError] = useState("");
  const [busy,setBusy] = useState(false);
  const [loading,setLoading] = useState(false);
  const [refresh,setRefresh] = useState(0);
  const [kind,setKind] = useState("movement");
  const [status,setStatus] = useState("planned");
  const [currency,setCurrency] = useState("RUB");
  const [paymentDate,setPaymentDate] = useState(today);
  const [confirmationDates,setConfirmationDates] = useState<Record<number,string>>({});
  const requestKey = useRef<string | null>(null);
  const pendingBody = useRef<unknown>(null);
  useEffect(() => {
    let active = true;
    setLoading(true); setReport(null); setError("");
    void getCashflow(start,end).then(data => {if(active) setReport(data);}).catch(reason => {if(active) setError(String(reason.message));}).finally(() => {if(active) setLoading(false);});
    return () => {active=false;};
  },[start,end,refresh]);
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if(!canWrite || busy) return;
    const form=event.currentTarget;
    const values=new FormData(form);
    if(!pendingBody.current) {
      requestKey.current ??= crypto.randomUUID();
      pendingBody.current={request_key:requestKey.current,kind,direction:values.get("direction"),status:kind==="opening"?"actual":status,
        planned_date:paymentDate,actual_date:kind==="opening"||status==="actual"?paymentDate:null,
        amount:String(values.get("amount")).replace(",","."),currency,exchange_rate:currency==="RUB"?"1":String(values.get("rate")).replace(",","."),
        category:kind==="opening"?"opening":values.get("category"),reference:values.get("reference")};
    }
    setBusy(true); setError("");
    try {await createCashEntry(pendingBody.current); requestKey.current=null; pendingBody.current=null; form.reset();setRefresh(value=>value+1);}
    catch(reason) {setError(reason instanceof Error?reason.message:"Ошибка сохранения");}
    finally {setBusy(false);}
  }
  async function action(id: number, next: string) {
    if(busy || !canWrite) return;
    if(next==="cancelled" && !window.confirm("Отменить операцию? Запись и история сохранятся.")) return;
    setBusy(true);setError("");
    try {await changeCashEntry(id,next,next==="actual"?(confirmationDates[id]??today()):undefined);setRefresh(value=>value+1);}
    catch(reason) {setError(reason instanceof Error?reason.message:"Ошибка сохранения");}
    finally {setBusy(false);}
  }
  return <section className="work-section cashflow-panel" aria-labelledby="cashflow-title">
    <h2 id="cashflow-title">Движение денег и платёжный календарь</h2>
    <p>Учитываются только внесённые вами денежные операции. Выручка и прибыль WB не означают поступление на счёт. Закупки и расходы из других разделов автоматически сюда не добавляются: внесите оплату один раз и укажите ссылку или номер документа.</p>
    <div className="cashflow-controls"><label>Начало периода<input aria-label="Начало периода ДДС" type="date" value={start} onChange={event=>setStart(event.target.value)}/></label><label>Конец периода<input aria-label="Конец периода ДДС" type="date" value={end} onChange={event=>setEnd(event.target.value)}/></label><button type="button" className="secondary-button" disabled={loading} onClick={()=>setRefresh(value=>value+1)}>Обновить календарь</button></div>
    <details className="cabinet-disclosure" open={compact ? undefined : true}><summary>Счета, кошельки и выписки</summary><CashAccountsPanel role={role} start={start} end={end}/></details>
    <h3>Исторический журнал — рублёвая оценка</h3>
    {error?<p role="alert">{error}</p>:null}{loading?<p role="status">Загрузка движения денег…</p>:null}
    {report?<><p>{report.opening_confirmed?"Начальный остаток подтверждён пользователем.":"Начальный остаток не подтверждён: показано движение внесённых сумм, а не баланс банковского счёта."}</p>{report.period_before_opening?<p role="alert">Период начинается раньше начального остатка. Выберите период с даты остатка.</p>:null}
      {!report.period_before_opening?<div className="cash-grid"><div className="stat"><span>Остаток на начало</span><strong>{money(report.opening_balance_minor)}</strong></div><div className="stat"><span>Поступления фактически</span><strong>{money(report.actual_inflow_minor)}</strong></div><div className="stat"><span>Выплаты фактически</span><strong>{money(report.actual_outflow_minor)}</strong></div><div className="stat"><span>Остаток по факту</span><strong>{money(report.actual_closing_minor)}</strong></div><div className="stat"><span>Остаток с планами</span><strong>{money(report.forecast_closing_minor)}</strong></div></div>:null}
      <p>Просроченные планы перенесены на начало периода: {money(report.overdue_planned_minor)}. План после подтверждения считается только по фактической дате.</p>
      <div className="cashflow-table"><table><caption>Операции ДДС, включая отменённые</caption><thead><tr><th>Дата плана / факта</th><th>Статья и документ</th><th>Сумма</th><th>Статус</th><th>Действия</th></tr></thead><tbody>{report.items.map(row=><tr key={row.id}><td>{row.planned_date}<br/>{row.actual_date??"—"}</td><td>{categories[row.category]}<br/>{row.reference||"Документ не указан"}</td><td>{row.direction==="inflow"?"+":"−"}{money(row.amount_minor)}<br/>{row.amount} {row.currency} × {row.exchange_rate}</td><td>{statuses[row.status]}</td><td>{canWrite&&row.status==="planned"?<><label>Дата оплаты<input aria-label={`Дата оплаты операции ${row.id}`} type="date" value={confirmationDates[row.id]??today()} onChange={event=>setConfirmationDates(dates=>({...dates,[row.id]:event.target.value}))}/></label><button disabled={busy} type="button" onClick={()=>void action(row.id,"actual")}>Оплачено / получено</button></>:null}{canWrite&&row.status!=="cancelled"?<button disabled={busy} type="button" onClick={()=>void action(row.id,"cancelled")}>Отменить</button>:null}</td></tr>)}</tbody></table></div>
      {!report.items.length?<p>Денежных операций пока нет.</p>:null}
      <details><summary>История подтверждений и отмен</summary><ul>{report.events.map(item=><li key={item.id}>Операция №{item.entry_id}: {statuses[item.status]} · {item.changed_at} {item.actor?`· ${item.actor}`:""}</li>)}</ul></details>
      {!report.period_before_opening?<details><summary>Остатки по дням</summary><ul>{report.daily.map(day=><li key={day.date}>{day.date}: факт {money(day.actual_balance_minor)}, с планами {money(day.forecast_balance_minor)}</li>)}</ul></details>:null}
    </>:null}
    {canWrite?<details className="cabinet-disclosure" open={compact ? undefined : true}><summary>Добавить денежную операцию</summary><form onSubmit={event=>void submit(event)} onChange={()=>{pendingBody.current=null;requestKey.current=null;}}><fieldset disabled={busy}><legend>Добавить денежную операцию</legend><div className="cashflow-controls">
      <label>Тип записи<select value={kind} onChange={event=>setKind(event.target.value)}><option value="movement">Движение денег</option><option value="opening">Начальный остаток</option></select></label>
      <label>Направление<select name="direction"><option value="inflow">Поступление / положительный остаток</option><option value="outflow">Выплата / отрицательный остаток</option></select></label>
      {kind!=="opening"?<label>Статус<select value={status} onChange={event=>setStatus(event.target.value)}><option value="planned">Запланировано</option><option value="actual">Фактически</option></select></label>:null}
      <label>{kind==="opening"?"Дата начального остатка":status==="actual"?"Дата платежа":"Плановая дата"}<input type="date" required value={paymentDate} onChange={event=>setPaymentDate(event.target.value)}/></label>
      <label>Сумма в валюте<input name="amount" inputMode="decimal" required pattern="[0-9]+([.,][0-9]{1,4})?"/></label>
      <label>Валюта<select value={currency} onChange={event=>setCurrency(event.target.value)}>{["RUB","CNY","USD","EUR"].map(item=><option key={item}>{item}</option>)}</select></label>
      {currency!=="RUB"?<label>Рублей за единицу валюты<input name="rate" inputMode="decimal" required pattern="[0-9]+([.,][0-9]{1,8})?"/></label>:null}
      {kind!=="opening"?<label>Статья<select name="category">{Object.entries(categories).filter(([key])=>key!=="opening").map(([key,label])=><option key={key} value={key}>{label}</option>)}</select></label>:null}
      <label>Номер документа / основание<input name="reference" maxLength={300}/></label><button className="primary-button" type="submit">{busy?"Сохраняется…":"Добавить операцию"}</button>
    </div>{kind==="opening"?<p>Остаток непосредственно перед началом указанной даты. Операции раньше этой даты не прибавляются повторно.</p>:null}</fieldset></form></details>:<p>Доступ только для чтения. Записывать денежные операции могут владелец и менеджер.</p>}
  </section>;
}
