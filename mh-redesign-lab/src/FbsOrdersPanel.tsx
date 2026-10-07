import { useCallback, useEffect, useRef, useState } from "react";
import { buildCsv } from "./csv";
import {
  FbsTooSoon, getFbsAnalytics, getFbsOrders, getFbsState, refreshFbs,
  type FbsAnalytics, type FbsOrders, type FbsState,
} from "./fbs-orders-api";
import "./FbsOrdersPanel.css";
import { saveBlob } from "./download";

const PAGE = 50;
const STATUSES = [["", "Все статусы"], ["processing", "Сборка"], ["in_transit", "В пути"], ["at_pickup", "В пункте выдачи"], ["delivered", "Выкуплен"], ["cancelled", "Отменён"], ["defect", "Брак"]];
const NO_SCOPE = "У токена нет доступа к категории «Маркетплейс»: добавьте её в токен WB, чтобы видеть FBS-заказы.";

export function fmtAge(hours: number | null): string {
  if (hours === null || hours === undefined) return "—";
  if (hours < 48) return `${Math.round(hours)} ч`;
  return `${Math.floor(hours / 24)} д ${Math.round(hours % 24)} ч`;
}
export const fmtPct = (v: number | null) => (v === null ? "—" : `${v.toLocaleString("ru-RU")} %`);
const fmtDate = (v: string | null) => (v ? new Date(v).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" }) : "—");

export function FbsOrdersPanel({ canWrite }: { canWrite: boolean }) {
  const [open, setOpen] = useState(false);
  const [state, setState] = useState<FbsState | null>(null);
  const [orders, setOrders] = useState<FbsOrders | null>(null);
  const [stats, setStats] = useState<FbsAnalytics | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [running, setRunning] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(false);
  const [filters, setFilters] = useState({ status: "", overdue: false, warehouse: "", days: 14, offset: 0 });
  const seq = useRef(0);
  const timer = useRef<number | undefined>(undefined);

  const load = useCallback(async () => {
    const id = ++seq.current;
    setLoading(true);
    setError(null);
    try {
      const [s, o, a] = await Promise.all([getFbsState(), getFbsOrders(filters, PAGE), getFbsAnalytics(filters.days)]);
      if (id !== seq.current) return; // a newer request owns the screen
      setState(s); setOrders(o); setStats(a);
    } catch (e) {
      if (id === seq.current) setError(e instanceof Error ? e.message : "Не удалось загрузить FBS-заказы");
    } finally {
      if (id === seq.current) setLoading(false);
    }
  }, [filters]);

  useEffect(() => { if (open) void load(); }, [open, load]);
  useEffect(() => () => { window.clearTimeout(timer.current); seq.current += 1; }, []);

  const startCooldown = (seconds: number) => {
    window.clearTimeout(timer.current);
    setCooldown(true);
    timer.current = window.setTimeout(() => setCooldown(false), Math.max(1, seconds) * 1000);
  };
  const refresh = async () => {
    setRunning(true); setNotice(null);
    try {
      const next = await refreshFbs(filters.days);
      setState(next);
      startCooldown(next.retry_after_seconds);
      await load();
    } catch (e) {
      if (e instanceof FbsTooSoon) { setNotice(e.message); startCooldown(e.retryAfter); }
      else setNotice(e instanceof Error ? e.message : "Не удалось обновить FBS-заказы");
    } finally { setRunning(false); }
  };
  const setFilter = (patch: Partial<typeof filters>) => setFilters((f) => ({ ...f, offset: 0, ...patch }));
  const exportCsv = () => {
    if (!orders) return;
    const rows = orders.items.map((o) => [o.order_id, o.article ?? "", o.nm_id, o.created_at ?? "", o.warehouse_id, o.supplier_status ?? "", o.wb_status ?? "", o.state_label, o.age_hours, o.overdue ? "просрочен" : ""]);
    void saveBlob(new Blob([buildCsv(["Заказ", "Артикул", "Артикул WB", "Создан", "Склад", "Статус продавца", "Статус WB", "Состояние", "Часов в работе", "Просрочка"], rows)], { type: "text/csv;charset=utf-8" }), "fbs-orders.csv");
  };

  const noScope = state?.has_marketplace_scope === false;
  const message = error ?? (noScope ? NO_SCOPE : state?.last_error ?? null);
  const blocked = running || cooldown;
  return (
    <section className="work-section fbs-panel" aria-label="FBS-заказы">
      <div className="fbs-head">
        <button className="secondary-button" type="button" aria-expanded={open} aria-controls="fbs-body" onClick={() => setOpen((v) => !v)}>
          {open ? "Скрыть FBS-заказы" : "FBS-заказы WB"}
        </button>
        {open ? <span className="fbs-updated">Обновлено: {fmtDate(state?.last_success_at ?? null)}</span> : <span className="fbs-updated">Список, просрочки и выкуп по сборочным заданиям</span>}
        {open && canWrite ? <button className="primary-button" type="button" disabled={blocked || noScope} onClick={() => void refresh()}>{running ? "Обновляем…" : cooldown ? "Обновлено, подождите" : "Обновить"}</button> : null}
      </div>
      {open ? <div id="fbs-body" className="fbs-body">
        {message ? <p className="data-banner stale" role="status">{message}</p> : null}
        {notice ? <p className="data-banner stale" role="status">{notice}</p> : null}
        {loading && !orders ? <p className="plan-empty" role="status">Загружаем FBS-заказы…</p> : null}
        {stats ? <>
          <dl className="fbs-strip" aria-label="Сводка по статусам">
            {stats.by_status.filter((s) => s.count > 0).map((s) => <div key={s.key}><dt>{s.label}</dt><dd>{s.count}</dd></div>)}
            <div><dt>Выкуп</dt><dd>{fmtPct(stats.buyout_pct)}</dd><small>{stats.final_count ? `из ${stats.final_count} завершённых` : "нет завершённых"}</small></div>
            <div><dt>Среднее время до выкупа</dt><dd>{fmtAge(stats.avg_delivery_hours)}</dd><small>{stats.avg_delivery_sample ? `по ${stats.avg_delivery_sample} заказам, оценка` : "нет данных"}</small></div>
            <div><dt>Просрочено</dt><dd>{stats.overdue_count}</dd><small>оценка: &gt; {state?.overdue_hours ?? 24} ч без подтверждения</small></div>
          </dl>
          {stats.by_day.length ? <details className="fbs-days"><summary>По дням</summary>
            <div className="fbs-scroll"><table className="fbs-table"><caption className="fbs-sr">Заказы по дням создания</caption>
              <thead><tr><th scope="col">День</th><th scope="col">Заказов</th><th scope="col">Выкуплено</th><th scope="col">Отменено/брак</th></tr></thead>
              <tbody>{stats.by_day.map((d) => <tr key={d.day}><td>{d.day}</td><td>{d.total}</td><td>{d.delivered}</td><td>{d.cancelled}</td></tr>)}</tbody></table></div>
            <p className="fbs-note">{stats.estimate_note}</p></details> : null}
        </> : null}
        <div className="fbs-filters">
          <label>Статус<select className="filter-select" value={filters.status} onChange={(e) => setFilter({ status: e.target.value })}>{STATUSES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></label>
          <label>Склад<select className="filter-select" value={filters.warehouse} onChange={(e) => setFilter({ warehouse: e.target.value })}><option value="">Все склады</option>{orders?.warehouses.map((w) => <option key={w.id} value={w.id}>Склад №{w.id} ({w.count})</option>)}</select></label>
          <label>Период<select className="filter-select" value={filters.days} onChange={(e) => setFilter({ days: Number(e.target.value) })}>{[7, 14, 30].map((d) => <option key={d} value={d}>{d} дней</option>)}</select></label>
          <label className="fbs-check"><input type="checkbox" checked={filters.overdue} onChange={(e) => setFilter({ overdue: e.target.checked })} />Только просроченные</label>
          <button className="ghost-button" type="button" disabled={!orders?.items.length} onClick={exportCsv}>Скачать CSV (эта страница)</button>
        </div>
        {orders && !orders.items.length && !loading ? <p className="plan-empty">{!state?.orders_total ? "FBS-заказов пока нет. Нажмите «Обновить», чтобы загрузить их из WB." : "По выбранным фильтрам заказов нет."}</p> : null}
        {orders?.items.length ? <div className="fbs-scroll" aria-busy={loading}><table className="fbs-table">
          <caption className="fbs-sr">FBS-заказы, новые сверху</caption>
          <thead><tr><th scope="col">Заказ</th><th scope="col">Товар</th><th scope="col">Создан</th><th scope="col">Склад</th><th scope="col">Статус продавца</th><th scope="col">Статус WB</th><th scope="col">В работе</th></tr></thead>
          <tbody>{orders.items.map((o) => <tr key={o.order_id} className={o.overdue ? "fbs-overdue" : undefined}>
            <td>{o.order_id}</td><td>{o.article ?? "—"}{o.nm_id ? <small> · {o.nm_id}</small> : null}</td><td>{fmtDate(o.created_at)}</td>
            <td>{o.warehouse_id ? `№${o.warehouse_id}` : "—"}</td><td>{o.supplier_status ?? "—"}</td><td>{o.wb_status ?? "—"} <small>({o.state_label})</small></td>
            <td>{fmtAge(o.age_hours)}{o.overdue ? <strong className="fbs-flag">⚠ просрочен</strong> : null}</td></tr>)}</tbody></table></div> : null}
        {orders && orders.total > PAGE ? <div className="fbs-pager">
          <button className="ghost-button" type="button" disabled={filters.offset === 0} onClick={() => setFilters((f) => ({ ...f, offset: Math.max(0, f.offset - PAGE) }))}>Назад</button>
          <span>{filters.offset + 1}–{Math.min(filters.offset + PAGE, orders.total)} из {orders.total}</span>
          <button className="ghost-button" type="button" disabled={filters.offset + PAGE >= orders.total} onClick={() => setFilters((f) => ({ ...f, offset: f.offset + PAGE }))}>Далее</button>
        </div> : null}
        <p className="fbs-note">Просрочка и сроки — оценка по времени последнего обновления; WB не отдаёт время смены статуса.</p>
      </div> : null}
    </section>
  );
}
