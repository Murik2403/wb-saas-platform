import { useEffect, useRef, useState } from "react";
import { getPriceAdvice, type DashboardPeriod, type PriceAdvice, type PriceAdviceItem, type PriceAdviceStatus, type ProductPriceRow } from "./data-source";
import "./PriceAdvisorPanel.css";

export type AdviceApply = { nmId: number; price: number; discount: number };

const TARGET_KEY = "mh.priceAdvisor.targetMargin";
const rub = (value: number | null) => (value === null ? "—" : `${Math.round(value).toLocaleString("ru-RU")} ₽`);
const pct = (value: number | null) => (value === null ? "—" : `${value.toLocaleString("ru-RU", { maximumFractionDigits: 1 })}%`);
const LABEL: Record<PriceAdviceStatus, { text: string; tone: string }> = {
  recommend: { text: "Рекомендация", tone: "info" },
  below_breakeven: { text: "Ниже безубыточности", tone: "danger" },
  ok: { text: "Менять не нужно", tone: "good" },
  insufficient_data: { text: "Мало данных", tone: "warn" },
  upload_in_progress: { text: "Идёт загрузка в WB", tone: "warn" },
};
const ORDER: PriceAdviceStatus[] = ["below_breakeven", "recommend", "ok", "insufficient_data", "upload_in_progress"];

function loadTarget(): string {
  try { return window.localStorage.getItem(TARGET_KEY) || "20"; } catch { return "20"; }
}

export function PriceAdvisorPanel({ period, prices, onApply }: { period: DashboardPeriod; prices: ProductPriceRow[]; onApply: (items: AdviceApply[]) => void }) {
  const [target, setTarget] = useState(loadTarget);
  const [advice, setAdvice] = useState<PriceAdvice | null>(null);
  const [state, setState] = useState<"idle" | "loading" | "error" | "ready">("idle");
  const [picked, setPicked] = useState<Set<number>>(new Set());
  const [showRest, setShowRest] = useState(false);
  const [applied, setApplied] = useState(0);
  const request = useRef<AbortController | null>(null);
  const targetNumber = Number(target);
  const targetValid = target.trim() !== "" && Number.isFinite(targetNumber) && targetNumber >= 0 && targetNumber <= 80;

  useEffect(() => () => request.current?.abort(), []);
  // A different period makes old advice stale: drop it instead of showing mismatched numbers.
  useEffect(() => { request.current?.abort(); setAdvice(null); setState("idle"); setPicked(new Set()); setApplied(0); }, [period.id, period.start, period.end]);

  const load = () => {
    if (!targetValid) return;
    try { window.localStorage.setItem(TARGET_KEY, String(targetNumber)); } catch { /* storage may be blocked */ }
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setState("loading"); setPicked(new Set()); setApplied(0);
    getPriceAdvice(period, targetNumber, prices, controller.signal).then(
      (data) => { if (request.current === controller) { setAdvice(data); setState("ready"); } },
      () => { if (request.current === controller) setState("error"); },
    );
  };

  const items = [...(advice?.items ?? [])].sort((a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status));
  const actionable = (item: PriceAdviceItem) => item.recommended_price !== null && item.current_discount !== null && (item.status === "recommend" || item.status === "below_breakeven");
  const main = items.filter((item) => item.status === "recommend" || item.status === "below_breakeven");
  const rest = items.filter((item) => !main.includes(item));
  const shown = showRest ? [...main, ...rest] : main;
  const toggle = (id: number) => setPicked((set) => { const next = new Set(set); if (next.has(id)) next.delete(id); else next.add(id); return next; });
  const apply = () => {
    const chosen = items.filter((item) => picked.has(item.nm_id) && actionable(item))
      .map((item) => ({ nmId: item.nm_id, price: item.recommended_price as number, discount: item.current_discount as number }));
    if (!chosen.length) return;
    onApply(chosen);
    setApplied(chosen.length); setPicked(new Set());
  };

  return (
    <section className="work-section wbx-panel pap" aria-label="Рекомендации по ценам">
      <div className="section-header"><div><span>Оценка</span><h2>Рекомендации по ценам</h2></div></div>
      <p className="pap-safe"><strong>Ничего не отправляется в WB.</strong> Проверьте черновик ниже и отправьте сами.</p>
      <p className="form-hint">Оценка, спрос на цену не прогнозируется: после повышения цены продажи могут упасть. Скидка сохраняется, меняется только базовая цена; цена поднимается не больше чем на {advice?.max_step_pct ?? 15}% за шаг, вниз не меняется.</p>
      <div className="pap-controls">
        <label>Целевая маржа, %
          <input type="number" min="0" max="80" step="1" inputMode="decimal" value={target} aria-invalid={!targetValid}
            onChange={(event) => setTarget(event.target.value)} />
        </label>
        <button type="button" className="primary-button" disabled={!targetValid || !prices.length} onClick={load}>
          {state === "ready" ? "Пересчитать" : "Рассчитать рекомендации"}
        </button>
        {!targetValid ? <span className="operation-error" role="alert">Введите число от 0 до 80</span> : null}
        {!prices.length ? <span className="form-hint">Цены WB ещё не загружены — рассчитать нечего.</span> : null}
      </div>

      {state === "loading" ? <p className="form-hint" role="status">Считаем рекомендации…</p> : null}
      {state === "error" ? <p className="operation-error" role="alert">Не удалось рассчитать рекомендации. <button type="button" className="pud-retry" onClick={load}>Повторить</button></p> : null}
      {state === "ready" && !items.length ? <p className="plan-empty">Нет товаров с экономикой за выбранный период</p> : null}
      {state === "ready" && items.length ? (
        <>
          <p className="form-hint" role="status">Рекомендаций: {main.length} из {items.length} SKU. Остальные — менять не нужно или мало данных (минимум {advice?.min_sales ?? 10} продаж за период, известная себестоимость).</p>
          {shown.length ? (
            <div className="pap-wrap">
              <table className="pap-table">
                <thead><tr><th><span className="sr-only">Выбрать</span></th><th>Артикул</th><th>Цена сейчас</th><th>Рекомендуем</th><th>Маржа сейчас → после</th><th>Безубыточная цена</th><th>Статус и причина</th></tr></thead>
                <tbody>
                  {shown.map((item) => {
                    const label = LABEL[item.status];
                    const can = actionable(item);
                    return (
                      <tr key={item.nm_id}>
                        <td>{can ? <input type="checkbox" aria-label={`Применить к черновику ${item.vendor_code || item.nm_id}`} checked={picked.has(item.nm_id)} onChange={() => toggle(item.nm_id)} /> : null}</td>
                        <td data-label="Артикул"><strong>{item.vendor_code || item.nm_id}</strong><small>{item.nm_id}</small></td>
                        <td className="pap-num" data-label="Цена сейчас">{rub(item.current_price)}{item.current_discount !== null ? <small>скидка {item.current_discount}% → {rub(item.current_final)}</small> : null}</td>
                        <td className="pap-num" data-label="Рекомендуем">{item.recommended_price !== null ? <>{rub(item.recommended_price)}<small>после скидки {rub(item.recommended_final)}</small></> : "—"}</td>
                        <td className="pap-num" data-label="Маржа">{pct(item.est_margin_now_pct)} → {pct(item.est_margin_after_pct)}</td>
                        <td className="pap-num" data-label="Безубыточная">{rub(item.breakeven_final)}</td>
                        <td data-label="Статус">
                          <span className={`status-pill ${label.tone}`}>{label.text}</span>
                          {item.note ? <small>{item.note}</small> : null}
                          <details className="pap-reason"><summary>Почему</summary><p>{item.reason}</p></details>
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ) : <p className="plan-empty">Для выбранной цели менять цены не нужно</p>}
          {rest.length ? (
            <button type="button" className="secondary-button" aria-expanded={showRest} onClick={() => setShowRest(!showRest)}>
              {showRest ? "Скрыть остальные" : `Показать остальные (${rest.length})`}
            </button>
          ) : null}
          <div className="pap-actions">
            <button type="button" className="primary-button" disabled={!picked.size} onClick={apply}>Применить выбранные к черновику{picked.size ? ` (${picked.size})` : ""}</button>
            {applied ? <span className="form-hint" role="status">В черновик добавлено: {applied}. В WB ничего не отправлено — проверьте таблицу «Цены и скидки» ниже.</span> : null}
          </div>
        </>
      ) : null}
    </section>
  );
}
