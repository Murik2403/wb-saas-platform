import { useCallback, useEffect, useId, useRef, useState, type ReactNode } from "react";
import { getDashboardSession, type ProductEconomicsRow } from "./data-source";
import { getRnp, saveRnpFavorites, saveRnpPlan, saveRnpTarget, type Rnp, type RnpDay, type RnpItem } from "./rnp-data";
import "./RnpPlanFact.css";

const FORECAST_NOT =
  "Прогноз не учитывает сезонность, акции, отсутствие товара на складе и зависимость спроса от цены. Это простое среднее, а не обещание продаж.";
const DEBOUNCE_MS = 800;
const MAX_PLAN = 100000;
const DASH = "—";

const num = (v: number | null, digits = 0) => (v === null ? DASH : v.toLocaleString("ru-RU", { maximumFractionDigits: digits }));
const rub = (v: number | null) => (v === null ? DASH : `${Math.round(v).toLocaleString("ru-RU")} ₽`);
const pct = (v: number | null) => (v === null ? DASH : `${v.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`);
const signed = (v: number, digits = 0) => `${v > 0 ? "+" : v < 0 ? "−" : ""}${Math.abs(v).toLocaleString("ru-RU", { maximumFractionDigits: digits })}`;
const mark = (v: number) => (v > 0 ? "▲" : v < 0 ? "▼" : "=");
const shortDate = (iso: string) => `${iso.slice(8, 10)}.${iso.slice(5, 7)}`;
const dayLabel = (iso: string) => new Date(`${iso}T00:00:00`).toLocaleDateString("ru-RU", { day: "2-digit", month: "2-digit", weekday: "short" });

/** Signed number AND a symbol, so the sign never depends on colour alone. */
function Dev({ abs, pctValue, unit }: { abs: number | null; pctValue?: number | null; unit: "шт" | "%" }) {
  const value = unit === "%" ? pctValue ?? null : abs;
  if (value === null) return <>{DASH}</>;
  return <span className={`rnp-dev ${value > 0 ? "rnp-up" : value < 0 ? "rnp-down" : ""}`}>{mark(value)} {signed(value, unit === "%" ? 1 : 0)}{unit === "%" ? "%" : ""}</span>;
}

type CellState = "saving" | "saved" | "error";

export function RnpPlanFact({ products }: { products: ProductEconomicsRow[] }) {
  const [open, setOpen] = useState(false);
  const [days, setDays] = useState(14);
  const [data, setData] = useState<Rnp | null>(null);
  const [status, setStatus] = useState<"idle" | "loading" | "error" | "ready">("idle");
  const [canWrite, setCanWrite] = useState(false);
  const [message, setMessage] = useState<{ tone: "ok" | "error"; text: string } | null>(null);
  const [search, setSearch] = useState("");
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [cells, setCells] = useState<Record<string, { state: CellState; text?: string }>>({});
  const seq = useRef(0);
  const controller = useRef<AbortController | null>(null);
  const timers = useRef<Record<string, number>>({});
  const reloadTimer = useRef<number | null>(null);
  const dataRef = useRef<Rnp | null>(null);
  dataRef.current = data;
  const searchId = useId();

  const load = useCallback((quiet = false) => {
    const mine = ++seq.current; // a response older than the latest request is ignored
    controller.current?.abort();
    const ctl = new AbortController();
    controller.current = ctl;
    if (!quiet) setStatus("loading");
    getRnp(days, ctl.signal).then(
      (next) => { if (mine === seq.current) { setData(next); setStatus("ready"); } },
      (error) => { if (mine === seq.current && !(error instanceof DOMException && error.name === "AbortError")) { if (!quiet) setStatus("error"); } },
    );
  }, [days]);

  useEffect(() => {
    if (!open) return;
    load();
    return () => { seq.current++; controller.current?.abort(); };
  }, [open, load]);

  useEffect(() => {
    if (!open) return;
    let alive = true;
    getDashboardSession().then((s) => { if (alive) setCanWrite(s.mode !== "demo" && ["owner", "manager"].includes(s.user.role.toLowerCase())); }, () => { if (alive) setCanWrite(false); });
    return () => { alive = false; };
  }, [open]);

  useEffect(() => () => {
    Object.values(timers.current).forEach((t) => window.clearTimeout(t));
    if (reloadTimer.current) window.clearTimeout(reloadTimer.current);
  }, []);

  const scheduleReload = () => {
    if (reloadTimer.current) window.clearTimeout(reloadTimer.current);
    reloadTimer.current = window.setTimeout(() => load(true), 400);
  };

  const pin = async (nmIds: number[]) => {
    try {
      await saveRnpFavorites(nmIds);
      setMessage({ tone: "ok", text: "Список закреплённых сохранён" });
      load();
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "Не удалось сохранить список" });
    }
  };

  const savePlan = async (nmId: number, day: string, text: string) => {
    const key = `${nmId}|${day}`;
    window.clearTimeout(timers.current[key]);
    const trimmed = text.trim();
    const current = dataRef.current?.items.find((i) => i.nm_id === nmId)?.days.find((d) => d.day === day)?.plan_orders ?? null;
    const value = trimmed === "" ? null : Number(trimmed);
    if (value !== null && (!Number.isInteger(value) || value < 0 || value > MAX_PLAN)) {
      setCells((c) => ({ ...c, [key]: { state: "error", text: `Целое число от 0 до ${MAX_PLAN.toLocaleString("ru-RU")}` } }));
      return;
    }
    if (value === current) { setDrafts(({ [key]: _gone, ...rest }) => rest); setCells(({ [key]: _gone, ...rest }) => rest); return; }
    setCells((c) => ({ ...c, [key]: { state: "saving" } }));
    try {
      await saveRnpPlan(nmId, { [day]: value });
      setData((d) => d && { ...d, items: d.items.map((i) => i.nm_id !== nmId ? i : { ...i, days: i.days.map((x) => x.day === day ? { ...x, plan_orders: value } : x) }) });
      setDrafts((d) => (d[key] === text ? (({ [key]: _gone, ...rest }) => rest)(d) : d));
      setCells((c) => ({ ...c, [key]: { state: "saved" } }));
      scheduleReload();
    } catch (error) {
      setCells((c) => ({ ...c, [key]: { state: "error", text: error instanceof Error ? error.message : "Не сохранено" } }));
    }
  };

  const typePlan = (nmId: number, day: string, text: string) => {
    const key = `${nmId}|${day}`;
    setDrafts((d) => ({ ...d, [key]: text }));
    setCells(({ [key]: _gone, ...rest }) => rest);
    window.clearTimeout(timers.current[key]);
    timers.current[key] = window.setTimeout(() => void savePlan(nmId, day, text), DEBOUNCE_MS);
  };

  const saveTarget = async (item: RnpItem, text: string) => {
    const trimmed = text.trim().replace(",", ".");
    const value = trimmed === "" ? null : Number(trimmed);
    if (value !== null && (!Number.isFinite(value) || value < 0 || value > 80)) { setMessage({ tone: "error", text: "Плановая маржа: число от 0 до 80" }); return; }
    if (value === item.plan_margin_pct) return;
    try {
      await saveRnpTarget(item.nm_id, value);
      setData((d) => d && { ...d, items: d.items.map((i) => i.nm_id !== item.nm_id ? i : { ...i, plan_margin_pct: value, margin_gap_pp: value !== null && i.forecast_margin_pct !== null ? Math.round((i.forecast_margin_pct - value) * 10) / 10 : null }) });
      setMessage({ tone: "ok", text: "Плановая маржа сохранена" });
    } catch (error) {
      setMessage({ tone: "error", text: error instanceof Error ? error.message : "Не удалось сохранить" });
    }
  };

  const pinned = data?.favorites ?? [];
  const q = search.trim().toLocaleLowerCase("ru-RU");
  const candidates = q ? products.filter((p) => !pinned.includes(Number(p.nmId)) && `${p.article} ${p.product} ${p.nmId}`.toLocaleLowerCase("ru-RU").includes(q)).slice(0, 8) : [];
  const full = pinned.length >= (data?.max_favorites ?? 10);

  return (
    <section className="work-section wbx-panel rnp" aria-label="План-факт по артикулам (РНП)">
      <div className="section-header"><div><span>Оценка</span><h2>План-факт по артикулам (РНП)</h2></div>
        <button type="button" className="secondary-button" aria-expanded={open} onClick={() => setOpen(!open)}>{open ? "Свернуть" : "Развернуть"}</button></div>
      {!open ? <p className="form-hint">Закрепите до 10 артикулов: план заказов по дням против факта, простой прогноз продаж и маржа. Загрузка — только при открытии.</p> : (
        <>
          <p className="rnp-banner" role="note"><strong>Как считается прогноз:</strong> прогноз = среднее за последние 7 дней; акции, сезонность и остатки не учитываются. {FORECAST_NOT}</p>
          <div className="rnp-controls">
            <label>Период
              <select value={days} onChange={(event) => setDays(Number(event.target.value))}>
                <option value={7}>7 дней</option><option value={14}>14 дней</option><option value={30}>30 дней</option>
              </select>
            </label>
            {canWrite ? (
              <div className="rnp-pin">
                <label htmlFor={searchId}>Закрепить артикул ({pinned.length} из {data?.max_favorites ?? 10})</label>
                <input id={searchId} type="search" value={search} disabled={full} placeholder={full ? "Достигнут максимум" : "Артикул или товар"} onChange={(event) => setSearch(event.target.value)} />
                {candidates.length ? (
                  <ul className="rnp-candidates" aria-label="Найденные товары">
                    {candidates.map((p) => <li key={p.nmId}><button type="button" onClick={() => { setSearch(""); void pin([...pinned, Number(p.nmId)]); }}>{p.article || p.nmId} <small>{p.product} · {p.nmId}</small></button></li>)}
                  </ul>
                ) : q ? <span className="form-hint">Ничего не найдено</span> : null}
              </div>
            ) : <span className="form-hint">Просмотр: закреплять артикулы и менять план могут владелец и менеджер.</span>}
          </div>
          <p className={message?.tone === "error" ? "operation-error" : "form-hint"} role={message?.tone === "error" ? "alert" : "status"}>{message?.text ?? ""}</p>

          {status === "loading" && !data ? <p className="form-hint" role="status">Загружаем план-факт…</p> : null}
          {status === "error" ? <p className="operation-error" role="alert">Не удалось загрузить план-факт. <button type="button" className="pud-retry" onClick={() => load()}>Повторить</button></p> : null}
          {data?.capped ? <p className="form-hint">Показаны последние {data.max_days} дней.</p> : null}
          {data?.ads_note ? <p className="form-hint">{data.ads_note}</p> : null}
          {status !== "error" && data && !data.items.length ? <p className="plan-empty">Артикулы не закреплены. Найдите товар выше и закрепите его, чтобы вести план по дням.</p> : null}
          {data?.items.map((item) => (
            <Article key={item.nm_id} item={item} canWrite={canWrite} drafts={drafts} cells={cells}
              onType={typePlan} onCommit={(nm, day, text) => void savePlan(nm, day, text)} onTarget={(text) => void saveTarget(item, text)}
              onUnpin={() => void pin(pinned.filter((n) => n !== item.nm_id))} />
          ))}
        </>
      )}
    </section>
  );
}

const ROWS: Array<{ id: string; label: string; cell: (d: RnpDay) => ReactNode; total?: (i: RnpItem) => ReactNode }> = [
  { id: "fact_orders", label: "Факт заказов, шт", cell: (d) => num(d.fact_orders), total: (i) => num(i.totals.fact_orders) },
  { id: "fact_orders_sum", label: "Факт заказов, ₽", cell: (d) => rub(d.fact_orders_sum), total: (i) => rub(i.totals.fact_orders_sum) },
  { id: "dev_abs", label: "Отклонение от плана, шт", cell: (d) => <Dev abs={d.deviation_abs} unit="шт" />, total: (i) => <Dev abs={i.totals.deviation_abs} unit="шт" /> },
  { id: "dev_pct", label: "Отклонение от плана, %", cell: (d) => <Dev abs={d.deviation_abs} pctValue={d.deviation_pct} unit="%" />, total: (i) => <Dev abs={i.totals.deviation_abs} pctValue={i.totals.deviation_pct} unit="%" /> },
  { id: "fact_sales", label: "Факт продаж (за вычетом возвратов), шт", cell: (d) => num(d.fact_sales), total: (i) => num(i.totals.fact_sales) },
  { id: "fact_sales_sum", label: "Факт продаж, ₽", cell: (d) => rub(d.fact_sales_sum), total: (i) => rub(i.totals.fact_sales_sum) },
  { id: "forecast", label: "Прогноз продаж, шт (среднее за 7 дней)", cell: (d) => num(d.forecast_sales, 1), total: (i) => num(i.totals.forecast_sales, 1) },
  { id: "price_before", label: "Цена до скидки", cell: (d) => rub(d.price_before) },
  { id: "price_after", label: "Цена после скидки", cell: (d) => rub(d.price_after) },
  { id: "ad", label: "Реклама, ₽", cell: (d) => rub(d.ad_spend), total: (i) => rub(i.totals.ad_spend) },
  { id: "drr", label: "ДРР, %", cell: (d) => pct(d.drr_pct), total: (i) => pct(i.totals.drr_pct) },
  { id: "stock", label: "Остаток на складах WB, шт", cell: (d) => num(d.stock) },
];

function Article({ item, canWrite, drafts, cells, onType, onCommit, onTarget, onUnpin }: {
  item: RnpItem; canWrite: boolean; drafts: Record<string, string>; cells: Record<string, { state: CellState; text?: string }>;
  onType: (nm: number, day: string, text: string) => void; onCommit: (nm: number, day: string, text: string) => void;
  onTarget: (text: string) => void; onUnpin: () => void;
}) {
  const [target, setTarget] = useState<string | null>(null);
  const targetValue = target ?? (item.plan_margin_pct === null ? "" : String(item.plan_margin_pct));
  const gap = item.margin_gap_pp;
  const name = item.vendor_code || String(item.nm_id);
  return (
    <article className="rnp-article" aria-label={`План-факт ${name}`}>
      <header className="rnp-head">
        <div><h3>{name}</h3><small>WB {item.nm_id}</small></div>
        <dl>
          <div><dt>Цена сейчас</dt><dd>{rub(item.current_price)}{item.current_discount !== null ? <small>скидка {item.current_discount}% → {rub(item.current_final)}</small> : null}</dd></div>
          <div><dt>Остаток сейчас</dt><dd>{num(item.stock_now)} шт</dd></div>
          <div><dt>Маржа по прогнозу</dt><dd>{pct(item.forecast_margin_pct)}{item.forecast_margin_pct === null && item.margin_note ? <small>{item.margin_note}</small> : <small>при текущей цене, модель как в рекомендациях по ценам</small>}</dd></div>
          <div><dt>Плановая маржа</dt><dd>{canWrite ? (
            <input type="number" min={0} max={80} step="0.5" inputMode="decimal" aria-label={`Плановая маржа ${name}, %`} value={targetValue}
              onChange={(event) => setTarget(event.target.value)}
              onBlur={() => { if (target !== null) { onTarget(target); setTarget(null); } }}
              onKeyDown={(event) => { if (event.key === "Enter") { onTarget(targetValue); setTarget(null); } }} />
          ) : pct(item.plan_margin_pct)}</dd></div>
          <div><dt>Разрыв с планом</dt><dd>{gap === null ? DASH : <span className={`rnp-dev ${gap > 0 ? "rnp-up" : gap < 0 ? "rnp-down" : ""}`}>{mark(gap)} {signed(gap, 1)} п.п.</span>}</dd></div>
        </dl>
        {canWrite ? <button type="button" className="secondary-button" onClick={onUnpin} aria-label={`Открепить ${name}`}>Открепить</button> : null}
      </header>
      <div className="rnp-wrap" tabIndex={0} role="region" aria-label={`Таблица по дням, ${name}`}>
        <table className="rnp-table">
          <thead>
            <tr>
              <th scope="col">Показатель</th>
              {item.days.map((d) => <th scope="col" key={d.day} className={d.is_today ? "rnp-today" : d.is_future ? "rnp-future" : ""}>{dayLabel(d.day)}{d.is_today ? <small>сегодня</small> : d.is_future ? <small>будущее</small> : null}</th>)}
              <th scope="col">Итого</th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <th scope="row">План заказов, шт</th>
              {item.days.map((d) => {
                const key = `${item.nm_id}|${d.day}`;
                const cell = cells[key];
                const value = drafts[key] ?? (d.plan_orders === null ? "" : String(d.plan_orders));
                return (
                  <td key={d.day} className={`rnp-plan ${d.is_today ? "rnp-today" : ""}`}>
                    {canWrite ? (
                      <>
                        <input type="number" min={0} max={MAX_PLAN} step={1} inputMode="numeric" value={value} aria-label={`План заказов ${name} на ${shortDate(d.day)}`}
                          aria-invalid={cell?.state === "error"} data-state={cell?.state ?? "idle"}
                          onChange={(event) => onType(item.nm_id, d.day, event.target.value)}
                          onBlur={() => { if (drafts[key] !== undefined) onCommit(item.nm_id, d.day, drafts[key]); }}
                          onKeyDown={(event) => { if (event.key === "Enter") onCommit(item.nm_id, d.day, value); }} />
                        <span className={`rnp-cell-state rnp-${cell?.state ?? "idle"}`} role={cell?.state === "error" ? "alert" : undefined}>
                          {cell?.state === "saving" ? "Сохраняем…" : cell?.state === "saved" ? "✓ Сохранено" : cell?.state === "error" ? `! ${cell.text ?? "Ошибка"}` : ""}
                        </span>
                      </>
                    ) : num(d.plan_orders)}
                  </td>
                );
              })}
              <td>{num(item.totals.plan_orders)}</td>
            </tr>
            {ROWS.map((row) => (
              <tr key={row.id}>
                <th scope="row">{row.label}</th>
                {item.days.map((d) => <td key={d.day} className={row.id === "forecast" ? "rnp-forecast" : ""} title={(d.is_today || d.is_future) && row.id.startsWith("dev") ? "День не завершён" : undefined}>{row.cell(d)}</td>)}
                <td>{row.total ? row.total(item) : DASH}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </article>
  );
}
