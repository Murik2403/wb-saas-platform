import { useCallback, useEffect, useRef, useState } from "react";
import type { FormEvent } from "react";
import { API_BASE_URL, apiHeaders } from "./data-source";
import "./ProductionWorkspace.css";

type Capacity = { capacity_known: boolean; pieces_per_day: number; workdays: number[]; horizon_days: number; fulfillment_lead_days: number; emergency_cover_days: number; expedited_fbo_lead_days: number; fbs_lead_days: number; note: string };
type Batch = { unit: string; id: number; material_name: string; blank_type: string; status: string; issued_meters: number; material_cost_rub: number; produced_units: number; scrap_units: number; remaining_units: number; unit_cost_rub: number; completion_movement_id: number | null; reversed_at: string | null };
type Workspace = {
  wip: { status: { open_batches: number; open_cost_rub: number; remaining_units: number; remaining_cost_rub: number }; batches: Batch[] };
  materials: { material_name: string; unit: string; available_meters: number }[];
  products: { nm_id: number; supplier_article: string; product_name: string; pack_size: number }[];
  capacity: Capacity;
  calendar: { date: string; planned_pieces: number | null; actual_pieces: number | null; capacity_pieces: number | null; overload_pieces: number | null; missing_pack_size: boolean; tasks: { task_key: string; product_name: string; supplier_article: string; status: string }[] }[];
  undated_tasks: { task_key: string }[];
};
const rub = (value: number) => value.toLocaleString("ru-RU", { style: "currency", currency: "RUB", maximumFractionDigits: 2 });
const today = () => new Date().toLocaleDateString("en-CA");
const number = (value: FormDataEntryValue | null) => Number(value);
async function request<T>(path = "", method = "GET", body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE_URL}/api/v1/production-workspace${path}`, { method, credentials: "include", headers: apiHeaders(body !== undefined), body: body === undefined ? undefined : JSON.stringify(body), signal });
  const data = await response.json().catch(() => null);
  if (!response.ok) throw new Error(typeof data?.detail === "string" ? data.detail : response.status === 422 ? "Проверьте значения полей: даты, положительные количества и рабочие дни." : `Не удалось выполнить запрос (${response.status}). Повторите попытку.`);
  return data as T;
}

export function ProductionWorkspace({ onChanged }: { onChanged?: () => void }) {
  const [data, setData] = useState<Workspace | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const pending = useRef(false);
  const [start, setStart] = useState(today);
  const [days, setDays] = useState(14);
  const [capacity, setCapacity] = useState<Capacity | null>(null);
  const issueId = useRef(crypto.randomUUID());
  const packageId = useRef(crypto.randomUUID());
  const [selectedMaterial, setSelectedMaterial] = useState("");
  const [closing, setClosing] = useState<number | null>(null);
  const load = useCallback(async (signal?: AbortSignal) => {
    const next = await request<Workspace>(`?start=${encodeURIComponent(start)}&days=${days}`, "GET", undefined, signal);
    setData(next);
    setCapacity(next.capacity);
  }, [start, days]);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    void load(controller.signal).catch((e: Error) => { if (!controller.signal.aborted) setError(e.message); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [load]);
  async function mutate(path: string, body: unknown, message: string, method = "POST", after?: () => void) {
    if (pending.current) return;
    pending.current = true;
    setBusy(true); setError(""); setNotice("");
    try {
      await request(path, method, body);
      after?.();
      setNotice(message);
      onChanged?.();
      try { await load(); }
      catch { setError("Операция сохранена, но список не обновился. Нажмите «Обновить производство»."); }
    } catch (e) { setError(e instanceof Error ? e.message : "Не удалось сохранить изменения"); }
    finally { pending.current = false; setBusy(false); }
  }
  function issue(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const element = event.currentTarget; const form = new FormData(element);
    void mutate("/wip/issue", { request_id: issueId.current, batch_date: form.get("date"), material_name: form.get("material"), blank_type: form.get("blank"), meters_used: number(form.get("meters")), note: form.get("note") }, "Сырьё выдано в НЗП. Остаток и стоимость обновлены.", "POST", () => { element.reset(); issueId.current = crypto.randomUUID(); });
  }
  function packageBlanks(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const element = event.currentTarget; const form = new FormData(element);
    void mutate("/wip/package", { request_id: packageId.current, nm_id: number(form.get("product")), qty_sets: number(form.get("quantity")), packaging_date: form.get("date"), note: form.get("note") }, "Готовые комплекты приняты на склад, заготовки списаны по FIFO.", "POST", () => { element.reset(); packageId.current = crypto.randomUUID(); });
  }
  function complete(event: FormEvent<HTMLFormElement>, id: number) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    void mutate(`/wip/${id}/complete`, { produced_units: number(form.get("good")), scrap_units: number(form.get("scrap")), note: form.get("note") }, "Партия заготовок закрыта.", "POST", () => setClosing(null));
  }
  const materials = data?.materials ?? [];
  const currentMaterial = materials.find(m => m.material_name === selectedMaterial) ?? materials[0];
  const materialUnit = currentMaterial?.unit || "м";
  return <section className="production-workspace" aria-label="НЗП и мощность производства">
    <header><h2>НЗП, заготовки и мощность</h2><button disabled={busy || loading} onClick={() => { setError(""); void load().catch((e: Error) => setError(e.message)); }}>Обновить производство</button></header>
    {error && <p role="alert" className="pw-error">{error}</p>}
    {notice && <p role="status" className="pw-notice">{notice}</p>}
    {loading && <p role="status">Загружаем НЗП и календарь…</p>}
    {!loading && !data && <button onClick={() => { setLoading(true); void load().catch((e: Error) => setError(e.message)).finally(() => setLoading(false)); }}>Повторить загрузку</button>}
    {data && <>
      <div className="pw-metrics"><p>Открыто партий <strong>{data.wip.status.open_batches}</strong></p><p>Сырьё в НЗП <strong>{rub(data.wip.status.open_cost_rub)}</strong></p><p>Заготовок на складе <strong>{data.wip.status.remaining_units} шт.</strong></p><p>Стоимость заготовок <strong>{rub(data.wip.status.remaining_cost_rub)}</strong></p></div>
      <div className="pw-forms">
        <form onSubmit={issue} onChange={() => { issueId.current = crypto.randomUUID(); }}><h3>Выдать сырьё в НЗП</h3><p>Количество указывается в единицах материала из справочника. Стоимость выдачи рассчитывается по FIFO.</p>
          {!materials.length && <p>Сначала добавьте материал и его остаток в разделе «Склад».</p>}
          <fieldset disabled={busy || !materials.length}><label>Дата выдачи<input name="date" type="date" defaultValue={today()} required /></label><label>Материал<select name="material" value={currentMaterial?.material_name ?? ""} onChange={e => setSelectedMaterial(e.target.value)} required>{materials.map(m => <option key={m.material_name} value={m.material_name}>{m.material_name} · {m.available_meters.toLocaleString("ru-RU")} {m.unit || "м"}</option>)}</select></label><label>Тип заготовки<input name="blank" required maxLength={200} /></label><label>Расход сырья, {materialUnit}<input name="meters" type="number" min="0.001" step="0.001" max="10000000" required /></label><label>Комментарий<input name="note" maxLength={2000} /></label><button type="submit">Выдать в НЗП</button></fieldset>
        </form>
        <form onSubmit={packageBlanks} onChange={() => { packageId.current = crypto.randomUUID(); }}><h3>Собрать готовые комплекты</h3><p>Заготовки спишутся по норме комплекта. Товар появится в готовом остатке.</p>{!data.products.length && <p>Сначала включите товар в производство и задайте его нормы.</p>}<fieldset disabled={busy || !data.products.length}><label>Товар<select name="product" required>{data.products.map(p => <option key={p.nm_id} value={p.nm_id}>{p.product_name || p.supplier_article || p.nm_id} · {p.pack_size} шт./комплект</option>)}</select></label><label>Дата комплектации<input name="date" type="date" defaultValue={today()} required /></label><label>Количество комплектов<input name="quantity" type="number" min="1" max="10000000" step="1" required /></label><label>Комментарий<input name="note" maxLength={2000} /></label><button type="submit">Собрать из заготовок</button></fieldset></form>
      </div>
      <h3>Партии НЗП и заготовок</h3>
      {!data.wip.batches.length && <p>Партий пока нет. Создайте первую выдачу сырья.</p>}
      <div className="pw-batches">{data.wip.batches.map(b => <article key={b.id} className="pw-batch"><h4>№{b.id} · {b.material_name} / {b.blank_type}</h4><p>{b.reversed_at ? "Отменена" : b.status === "open" ? "Сырьё в работе" : "Заготовки выпущены"} · Выдано {b.issued_meters} {b.unit} · {rub(b.material_cost_rub)}</p>{b.status !== "open" && !b.reversed_at && <p>Годных {b.produced_units} · Брак {b.scrap_units} · Осталось {b.remaining_units} шт. · {rub(b.unit_cost_rub)} / шт.</p>}
        {!b.reversed_at && <div className="pw-actions">{b.status === "open" ? <><button disabled={busy} onClick={() => setClosing(closing === b.id ? null : b.id)}>Закрыть партию</button><button disabled={busy} onClick={() => { if (window.confirm(`Отменить выдачу партии №${b.id} и вернуть сырьё на склад?`)) void mutate(`/wip/${b.id}/reverse`, { stage: "issue" }, "Выдача отменена, сырьё возвращено."); }}>Отменить выдачу</button></> : <button disabled={busy} onClick={() => { if (window.confirm(`Отменить выпуск заготовок №${b.id}? Партия вернётся в НЗП. Если заготовки уже использованы, отмена будет отклонена.`)) void mutate(`/wip/${b.id}/reverse`, { stage: "completion" }, "Выпуск отменён. Партия возвращена в НЗП."); }}>Отменить выпуск заготовок</button>}</div>}
        {closing === b.id && b.status === "open" && !b.reversed_at && <form onSubmit={e => complete(e, b.id)}><fieldset disabled={busy}><label>Годных заготовок, шт.<input name="good" type="number" min="1" max="10000000" step="1" required /></label><label>Брак, шт.<input name="scrap" type="number" min="0" max="10000000" step="1" defaultValue="0" required /></label><label>Комментарий<input name="note" maxLength={2000} /></label><p>Стоимость сырья распределяется на годные заготовки.</p><button type="submit">Подтвердить выпуск</button></fieldset></form>}
      </article>)}</div>
      {capacity && <form onSubmit={e => { e.preventDefault(); const { capacity_known, pieces_per_day, workdays, horizon_days, fulfillment_lead_days, emergency_cover_days, expedited_fbo_lead_days, fbs_lead_days, note } = capacity; void mutate("/capacity", { capacity_known, pieces_per_day, workdays, horizon_days, fulfillment_lead_days, emergency_cover_days, expedited_fbo_lead_days, fbs_lead_days, note }, "Мощность сохранена.", "PUT"); }}><h3>Настройки мощности</h3><fieldset disabled={busy}><label className="pw-check"><input type="checkbox" checked={capacity.capacity_known} onChange={e => setCapacity({ ...capacity, capacity_known: e.target.checked })} />Мощность известна</label><div className="pw-capacity-fields">{([{ key: "pieces_per_day", label: "Штук в рабочий день", min: capacity.capacity_known ? 1 : 0, max: 10000000 }, { key: "horizon_days", label: "Горизонт плана, дней", min: 1, max: 90 }, { key: "fulfillment_lead_days", label: "До склада WB, дней", min: 0, max: 365 }, { key: "emergency_cover_days", label: "Аварийный запас, дней", min: 1, max: 365 }, { key: "expedited_fbo_lead_days", label: "Ускоренная поставка FBO, дней", min: 0, max: 365 }, { key: "fbs_lead_days", label: "Поставка FBS, дней", min: 0, max: 365 }] as const).map(f => <label key={f.key}>{f.label}<input type="number" required step="1" min={f.min} max={f.max} value={capacity[f.key]} onChange={e => setCapacity({ ...capacity, [f.key]: Number(e.target.value) })} /></label>)}</div><div className="pw-actions" role="group" aria-label="Рабочие дни">{["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"].map((d, i) => <label className="pw-check" key={d}><input type="checkbox" checked={capacity.workdays.includes(i)} onChange={e => setCapacity({ ...capacity, workdays: e.target.checked ? [...capacity.workdays, i].sort() : capacity.workdays.filter(x => x !== i) })} />{d}</label>)}</div><label>Комментарий к мощности<input maxLength={2000} value={capacity.note} onChange={e => setCapacity({ ...capacity, note: e.target.value })} /></label><button disabled={!capacity.workdays.length} type="submit">Сохранить мощность</button></fieldset></form>}
      <h3>Календарь загрузки</h3><p>Сохранённые задания производства. Комплекты переведены в штуки по норме товара; календарь не создаёт и не переносит задания.</p><div className="pw-actions"><label>Начало календаря<input type="date" value={start} onChange={e => { if (e.target.value) setStart(e.target.value); }} /></label><label>Период<select value={days} onChange={e => setDays(Number(e.target.value))}><option value={7}>7 дней</option><option value={14}>14 дней</option><option value={30}>30 дней</option><option value={90}>90 дней</option></select></label></div>
      {!!data.undated_tasks.length && <p className="pw-error">Заданий без даты: {data.undated_tasks.length}. Они не включены в дневную загрузку.</p>}
      {!data.calendar.some(d => d.tasks.length) && <p>В выбранном периоде нет сохранённых производственных заданий.</p>}
      <div className="pw-table-scroll"><table><caption>Загрузка по дням, в штуках изделий</caption><thead><tr><th>Дата</th><th>План</th><th>Факт</th><th>Мощность</th><th>Превышение</th><th>Задания</th></tr></thead><tbody>{data.calendar.map(d => <tr key={d.date} className={d.overload_pieces ? "pw-overload" : ""}><td>{d.date}</td><td>{d.planned_pieces ?? "Неизвестно"}</td><td>{d.actual_pieces ?? "Неизвестно"}</td><td>{d.capacity_pieces ?? "Не задана"}</td><td>{d.overload_pieces ?? "—"}</td><td>{d.missing_pack_size && <span>Не заполнен размер комплекта. </span>}{d.tasks.map(t => <div key={t.task_key}>{t.product_name || t.supplier_article} · {t.status}</div>)}</td></tr>)}</tbody></table></div>
    </>}
  </section>;
}
