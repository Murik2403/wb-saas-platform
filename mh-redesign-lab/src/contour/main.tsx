import React, { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { ArrowUpRight, CalendarDays, ChevronDown, Download, Table2, ArrowLeft } from 'lucide-react';
import { expenses, number, sales, stocks } from './data';
import './styles.css';

function DataTable({ headers, rows }: { headers: string[]; rows: (string | number)[][] }) {
  return <details className="data-table"><summary><Table2 size={14}/>Показать данные<ChevronDown size={14}/></summary><div className="table-scroll"><table><thead><tr>{headers.map(h => <th key={h}>{h}</th>)}</tr></thead><tbody>{rows.map((r,i) => <tr key={i}>{r.map((c,j) => <td key={j}>{c}</td>)}</tr>)}</tbody></table></div></details>;
}
function App() {
  const [period, setPeriod] = useState(7);
  const [comparison, setComparison] = useState(true);
  const [active, setActive] = useState<number | null>(null);
  const points = sales.slice(-period);
  const selected = points[active ?? points.length - 1];
  const x = (i: number) => 52 + i * 560 / (points.length - 1);
  const y = (v: number) => 248 - v * .8;
  const path = (key: 'current' | 'previous') => points.map((p,i) => `${i ? 'L' : 'M'}${x(i)},${y(p[key])}`).join(' ');
  function download() {
    const csv = '\uFEFFУчебные данные MARKETSHELPER\nДата;Выручка, руб.;Предыдущий период, руб.\n' + points.map(p => `${p.date};${p.current*1000};${p.previous*1000}`).join('\n');
    const url = URL.createObjectURL(new Blob([csv], { type:'text/csv;charset=utf-8' }));
    const a = document.createElement('a'); a.href=url; a.download='marketshelper-demo-sales.csv'; a.click(); URL.revokeObjectURL(url);
  }
  return <div className="contour-app">
    <header className="topbar"><a className="brand" href="/contour.html"><span>м</span>marketshelper</a><nav aria-label="Блоки аналитики"><a className="active" href="#sales">Обзор</a><a href="#expenses">Расходы</a><a href="#stock">Остатки</a></nav><div className="top-note"><span/>Локальный просмотр</div></header>
    <main><div className="intro"><div><div className="eyebrow">КОНТУР / ТОЧНОСТЬ <span>01 — 03</span></div><h1>Цифры, которые можно проверить.</h1><p>Продажи, расходы и запасы магазина — на одном экране.</p></div><span className="demo">Демо · учебные данные</span></div>
      <div className="toolbar"><div className="store"><span className="store-icon">М</span><div>Демо-магазин<small>Wildberries · пример оформления</small></div></div><div className="tools"><label className="period"><CalendarDays size={16}/><select aria-label="Период продаж" value={period} onChange={e => {setPeriod(Number(e.target.value));setActive(null);}}><option value={7}>7 дней</option><option value={3}>3 дня</option></select></label><button className="export" onClick={download}><Download size={16}/>Экспорт продаж</button></div></div>
      <div className="chart-grid">
        <section className="panel sales-panel" id="sales" aria-labelledby="sales-title"><div className="panel-head"><div><span className="section-no">01 / ПРОДАЖИ</span><h2 id="sales-title">Динамика продаж</h2><p>Выручка по дням, тыс. ₽</p></div><span className="unit">₽</span></div>
          <div className="sales-readout" aria-live="polite"><strong>{number(selected.current*1000)} <span>₽</span></strong><span>{selected.date} · учебный пример</span></div>
          <svg className="line-chart" viewBox="0 0 650 286" role="group" aria-label="График выручки. Точки доступны с клавиатуры; точные значения также в таблице.">
            {[0,50,100,150,200,250].map(v => <g key={v}><line x1="52" x2="612" y1={y(v)} y2={y(v)} className="gridline"/><text x="38" y={y(v)+4} textAnchor="end">{v}</text></g>)}
            {comparison && <path d={path('previous')} className="previous-line"/>}<path d={path('current')} className="current-line"/>
            {points.map((p,i) => <g key={p.date}>{comparison && <circle cx={x(i)} cy={y(p.previous)} r="3.5" className="previous-dot"/>}<circle cx={x(i)} cy={y(p.current)} r={active===i ? 7 : 5} className="current-dot" tabIndex={0} role="button" aria-label={`${p.date}: ${number(p.current*1000)} рублей`} onMouseEnter={() => setActive(i)} onFocus={() => setActive(i)} onClick={() => setActive(i)} onKeyDown={e => {if(e.key==='Enter'||e.key===' ') {e.preventDefault();setActive(i);}}}><title>{p.date}: {number(p.current*1000)} ₽</title></circle><text x={x(i)} y="275" textAnchor="middle">{p.date}</text></g>)}
          </svg><div className="legend"><span><i className="accent"/>Выбранный период</span><button aria-pressed={comparison} onClick={() => setComparison(!comparison)} className={!comparison ? 'disabled-series' : ''}><i className="muted"/>Предыдущий период</button></div>
          <DataTable headers={['Дата','Выручка, ₽','Предыдущий период, ₽']} rows={points.map(p => [p.date,number(p.current*1000),number(p.previous*1000)])}/>
        </section>
        <section className="panel" id="expenses" aria-labelledby="expenses-title"><div className="panel-head"><div><span className="section-no">02 / ЭКОНОМИКА ТОВАРА</span><h2 id="expenses-title">Расходы на единицу</h2><p>Из чего складываются затраты, ₽</p></div><span className="unit">₽/шт.</span></div>
          <div className="expense-chart">{expenses.map(e => <div className="expense-row" key={e.label}><div className="bar-label">{e.label}<strong>{number(e.value)} ₽</strong></div><div className="bar-track"><div className={`bar ${e.color}`} style={{width:`${e.value/520*100}%`}}/></div></div>)}</div>
          <div className="expense-total"><span>По показанным статьям</span><strong>{number(expenses.reduce((s,e)=>s+e.value,0))} <small>₽/шт.</small></strong></div><p className="footnote">Без налогов, хранения, возвратов и прочих расходов. Учебный расчёт для одного товара.</p>
          <DataTable headers={['Статья','₽/шт.']} rows={expenses.map(e => [e.label,e.value])}/>
        </section>
        <section className="panel stock-panel" id="stock" aria-labelledby="stock-title"><div className="panel-head"><div><span className="section-no">03 / ЗАПАСЫ</span><h2 id="stock-title">Остатки по товарам</h2><p>Внутренний склад и Wildberries, шт.</p></div><div className="legend"><span><i className="accent"/>WB</span><span><i className="sand"/>Внутренний склад</span></div></div>
          <div className="stock-layout"><div className="stock-chart">{stocks.map(s => <div className="stock-row" key={s.name}><span>{s.name}</span><div className="stock-bars"><div><div className="bar accent" style={{width:`${s.wb/400*100}%`}}/><b>{s.wb}</b></div><div><div className="bar sand" style={{width:`${s.internal/400*100}%`}}/><b>{s.internal}</b></div></div></div>)}<div className="axis">{[0,100,200,300,400].map(v=><span key={v}>{v}</span>)}</div></div><aside className="stock-summary"><span className="section-no">НА СКЛАДАХ WB</span><strong>{number(stocks.reduce((s,r)=>s+r.wb,0))}<small> шт.</small></strong><p>Остатки трёх товаров<br/>из учебного примера.</p><a href="#stock-data">Сверить значения <ArrowUpRight size={15}/></a></aside></div>
          <div id="stock-data"><DataTable headers={['Товар','WB, шт.','Внутренний склад, шт.']} rows={stocks.map(s=>[s.name,s.wb,s.internal])}/></div>
        </section>
      </div><footer><span>MARKETSHELPER <b>/</b> Контур · локальный редизайн</span><a href="/"><ArrowLeft size={14}/>Текущий интерфейс</a><span>Учебные данные · API не подключён</span></footer>
    </main>
  </div>;
}
createRoot(document.getElementById('root')!).render(<React.StrictMode><App/></React.StrictMode>);
