import { useEffect, useRef, useState } from "react";
import { Command, Menu, X, LayoutDashboard, Package, Megaphone, WalletCards, Factory, Truck, Bot, BarChart3, ClipboardCheck, SlidersHorizontal } from "lucide-react";
import { ThemeToggle } from "./ThemeToggle";
import { OzonCabinetViews, type OzonCabinetPage } from "./OzonCabinetViews";
import type { MarketplaceSession } from "./marketplace-context";
import "./OzonCabinetShell.css";
const navigation = [
  ["today","Сегодня",LayoutDashboard],["stock","Склад",Package],["ads","Реклама",Megaphone],["finance","Финансы",WalletCards],
  ["production","Производство",Factory],["supply","Поставки",Truck],["agents","Агенты",Bot],["reports","Отчеты",BarChart3],
  ["products","Товары",Package],["control","Контроль",ClipboardCheck],["settings","Настройки",SlidersHorizontal],
] as const;
const titles:Record<OzonCabinetPage,string>={today:"Продажи и действия Ozon",stock:"Остатки Ozon",ads:"Реклама Ozon",finance:"Деньги и прибыль Ozon",production:"Производство для маркетплейсов",supply:"Отправления и возвраты Ozon",agents:"Рекомендации и автоматизация Ozon",reports:"Отчёты Ozon",products:"Товары и экономика Ozon",control:"Контроль данных Ozon",settings:"Подключение и настройки Ozon"};
function initialPage():OzonCabinetPage { const value=new URL(location.href).searchParams.get("view");return value==="assistant"?"agents":navigation.some(([id])=>id===value)?value as OzonCabinetPage:"today"; }
function dayOffset(value:string,days:number){const day=new Date(value+"T12:00:00Z");day.setUTCDate(day.getUTCDate()+days);return day.toISOString().slice(0,10);}
export function ozonPeriod(days:number){const parts=new Intl.DateTimeFormat("en",{timeZone:"Europe/Moscow",year:"numeric",month:"2-digit",day:"2-digit"}).formatToParts(new Date());const part=(type:string)=>parts.find(p=>p.type===type)?.value;const today=`${part('year')}-${part('month')}-${part('day')}`;const end=dayOffset(today,-1);return {start:dayOffset(end,1-days),end};}
function validRange(start:string,end:string){if(!/^\d{4}-\d{2}-\d{2}$/.test(start)||!/^\d{4}-\d{2}-\d{2}$/.test(end))return false;const a=Date.parse(start+"T12:00:00Z"),b=Date.parse(end+"T12:00:00Z");return Number.isFinite(a)&&Number.isFinite(b)&&new Date(a).toISOString().slice(0,10)===start&&new Date(b).toISOString().slice(0,10)===end&&a<=b&&(b-a)/86400000<=30;}
function initialPeriod(){const query=new URL(location.href).searchParams;const start=query.get('start'),end=query.get('end');return start&&end&&validRange(start,end)?{start,end}:ozonPeriod(query.get('period')==='7d'?7:30);}

export function OzonCabinetShell({session}:{session:MarketplaceSession}){
  const [page,setPage]=useState(initialPage),[period,setPeriod]=useState(initialPeriod),[menu,setMenu]=useState(false),[revision,setRevision]=useState(0);
  const [draftStart,setDraftStart]=useState(period.start),[draftEnd,setDraftEnd]=useState(period.end),[dateError,setDateError]=useState("");
  const rail=useRef<HTMLElement>(null),menuButton=useRef<HTMLButtonElement>(null);
  useEffect(()=>{if(!menu)return;const previous=document.activeElement;const node=rail.current;if(!node)return;const items=()=>Array.from(node.querySelectorAll<HTMLButtonElement>('button:not([disabled])'));items()[0]?.focus();const key=(event:KeyboardEvent)=>{if(event.key==='Escape'){event.preventDefault();setMenu(false);}else if(event.key==='Tab'){const buttons=items(),first=buttons[0],last=buttons[buttons.length-1];if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus();}}};document.addEventListener('keydown',key);return()=>{document.removeEventListener('keydown',key);if(previous instanceof HTMLElement&&previous.isConnected)previous.focus();else menuButton.current?.focus();};},[menu]);
  useEffect(()=>{const url=new URL(location.href);url.searchParams.set("marketplace","ozon");url.searchParams.set("view",page==='agents'?'assistant':page);url.searchParams.set("period","custom");url.searchParams.set("start",period.start);url.searchParams.set("end",period.end);url.searchParams.delete("shop");history.replaceState(null,"",url);},[page,period]);
  const choose=(next:OzonCabinetPage)=>{setPage(next);setMenu(false);setDateError("");};
  const applyDays=(days:number)=>{const next=ozonPeriod(days);setPeriod(next);setDraftStart(next.start);setDraftEnd(next.end);setDateError("");};
  const applyRange=()=>{if(!validRange(draftStart,draftEnd)){setDateError("Выберите от 1 до 31 дня. Длинный помесячный отчёт доступен в «Финансах».");return;}setDateError("");setPeriod({start:draftStart,end:draftEnd});};
  return <div className="app-shell ozon-cabinet-shell">
    <aside ref={rail} className={`side-rail ozon-rail ${menu?'ozon-rail-open':''}`} aria-label="Навигация кабинета Ozon" role={menu?'dialog':undefined} aria-modal={menu?true:undefined}>
      <div className="brand-row"><div className="brand-mark"><Command size={18}/></div><div><p className="brand-title">MARKETSHELPER</p><p className="brand-subtitle">кабинет Ozon</p></div></div>
      {menu&&<button type="button" className="icon-button ozon-close-menu" aria-label="Закрыть меню Ozon" onClick={()=>setMenu(false)}><X/></button>}
      <nav className="nav-list">{navigation.map(([id,label,Icon])=><button type="button" key={id} className={`nav-item ${page===id?'active':''}`} aria-current={page===id?'page':undefined} onClick={()=>choose(id)}><Icon size={18}/><span>{label}</span></button>)}</nav>
      <div className="side-status"><p>Ozon · {session.is_demo?'демо':session.can_write?'рабочий кабинет':'просмотр'}</p><span>Данные WB не подставляются</span></div>
    </aside>
    <main className="workspace">
      <header className="topbar"><button ref={menuButton} type="button" className="icon-button ozon-menu-toggle" aria-label="Открыть меню Ozon" aria-expanded={menu} onClick={()=>setMenu(v=>!v)}><Menu/></button><div><span className="eyebrow">Ozon · {navigation.find(([id])=>id===page)?.[1]}</span><h1>{titles[page]}</h1></div><div className="topbar-actions"><ThemeToggle/><button type="button" className="secondary-button" onClick={()=>setRevision(v=>v+1)}>Обновить Ozon</button><a className="secondary-button" href="https://marketshelper.ru/account">Аккаунт</a></div></header>
      <section className="panel ozon-period-bar" aria-label="Период кабинета Ozon"><div><button type="button" className="secondary-button" onClick={()=>applyDays(7)}>7 дней</button><button type="button" className="secondary-button" onClick={()=>applyDays(30)}>30 дней</button></div><label>Ozon: начало периода<input type="date" value={draftStart} onChange={e=>setDraftStart(e.target.value)}/></label><label>Ozon: конец периода<input type="date" value={draftEnd} onChange={e=>setDraftEnd(e.target.value)}/></label><button type="button" className="secondary-button" onClick={applyRange}>Применить период</button>{dateError&&<p role="alert">{dateError}</p>}</section>
      {session.is_demo&&<p className="data-banner" role="status">Демонстрационный кабинет Ozon: внешние операции и запись отключены.</p>}
      <OzonCabinetViews key={`${page}:${period.start}:${period.end}:${revision}`} page={page} start={period.start} end={period.end} canWrite={session.can_write&&!session.is_demo} isOwner={session.role==='owner'&&!session.is_demo} role={session.role}/>
      <footer className="ozon-cabinet-footer"><a href="https://marketshelper.ru/training">Видеоуроки</a><a href="https://marketshelper.ru/billing">Подписка</a><a href="https://marketshelper.ru/contacts">Поддержка</a></footer>
    </main>
  </div>;
}
