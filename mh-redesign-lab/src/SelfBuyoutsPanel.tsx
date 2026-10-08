import { useEffect, useMemo, useRef, useState, type FormEvent } from "react";
import type { DashboardPeriod } from "./data-source";
import { buildCsv } from "./csv";
import {
  BuyoutError, createSelfBuyout, deleteSelfBuyout, formatRub, getSelfBuyouts, parseRubToKop, updateSelfBuyout,
  type BuyoutDelta, type BuyoutGroup, type BuyoutRecord, type BuyoutReport,
} from "./self-buyouts-source";
import "./SelfBuyoutsPanel.css";
import { saveBlob } from "./download";

export type BuyoutProduct = { nmId: string; article: string; product: string };
type FormState = { day: string; nm: string; units: string; paid: string; cashback: string; format: string; note: string };
type Fields = Partial<Record<keyof FormState, string>>;

const GROUPS: Array<[BuyoutGroup, string]> = [["day", "День"], ["week", "Неделя"], ["month", "Месяц"]];
const SERVER_FIELD: Record<string, keyof FormState> = { day: "day", nm_id: "nm", units: "units", buyout_amount_kop: "paid", cashback_kop: "cashback", format: "format", note: "note" };
const localToday = () => new Date().toLocaleDateString("en-CA");
const shiftDays = (iso: string, days: number) => { const d = new Date(`${iso}T12:00:00`); d.setDate(d.getDate() + days); return d.toLocaleDateString("en-CA"); };
const ruDate = (iso: string) => iso.split("-").reverse().join(".");
const kopText = (kop: number) => `${Math.floor(kop / 100)},${String(kop % 100).padStart(2, "0")}`;
const emptyForm = (): FormState => ({ day: localToday(), nm: "", units: "1", paid: "", cashback: "", format: "", note: "" });

function groupLabel(start: string, group: BuyoutGroup): string {
  if (group === "day") return ruDate(start);
  if (group === "week") return `Неделя с ${ruDate(start)}`;
  const [y, m] = start.split("-").map(Number);
  return new Date(y, m - 1, 1).toLocaleDateString("ru-RU", { month: "long", year: "numeric" });
}

/** Signed change against the previous equal window; arrow + sign + words, never colour alone. */
function deltaView(d: BuyoutDelta, money: boolean): { text: string; label: string } {
  const fmt = (n: number) => (money ? formatRub(Math.abs(n)) : `${Math.abs(n).toLocaleString("ru-RU")} шт`);
  if (d.delta === 0) return { text: "• без изменений", label: "без изменений к прошлому периоду" };
  const up = d.delta > 0;
  const pct = d.delta_pct === null ? "" : ` (${up ? "+" : "−"}${Math.abs(d.delta_pct).toLocaleString("ru-RU")}%)`;
  const base = d.previous === 0 ? " · раньше 0" : "";
  return { text: `${up ? "▲ +" : "▼ −"}${fmt(d.delta)}${pct}${base}`, label: `${up ? "рост" : "снижение"} на ${fmt(d.delta)}${pct} к прошлому периоду` };
}

function validate(form: FormState): { fields: Fields; payload?: { day: string; nm_id: number; units: number; buyout_amount_kop: number; cashback_kop: number; format: string | null; note: string | null } } {
  const fields: Fields = {};
  const today = localToday();
  if (!/^\d{4}-\d{2}-\d{2}$/.test(form.day)) fields.day = "Укажите дату";
  else if (form.day < shiftDays(today, -400) || form.day > shiftDays(today, 1)) fields.day = "Дата: не раньше 400 дней назад и не позже завтрашнего дня";
  const nm = Number(form.nm);
  if (!form.nm.trim() || !Number.isInteger(nm) || nm <= 0) fields.nm = "Выберите товар";
  const units = Number(form.units);
  if (!/^\d+$/.test(form.units.trim()) || units < 1 || units > 10000) fields.units = "Количество: целое число от 1 до 10 000";
  const paid = parseRubToKop(form.paid);
  if (paid === null) fields.paid = "Введите сумму в рублях, например 1250,50";
  else if (paid > 100_000_000) fields.paid = "Сумма: не более 1 000 000 ₽";
  const cashback = form.cashback.trim() === "" ? 0 : parseRubToKop(form.cashback);
  if (cashback === null) fields.cashback = "Введите сумму в рублях или оставьте пустым";
  else if (cashback > 100_000_000) fields.cashback = "Кешбэк: не более 1 000 000 ₽";
  if (form.format.trim().length > 40) fields.format = "Не длиннее 40 символов";
  if (form.note.trim().length > 200) fields.note = "Не длиннее 200 символов";
  if (Object.keys(fields).length) return { fields };
  return { fields, payload: { day: form.day, nm_id: nm, units, buyout_amount_kop: paid as number, cashback_kop: cashback as number, format: form.format.trim() || null, note: form.note.trim() || null } };
}

export function SelfBuyoutsPanel({ role, products, period, confirm }: {
  role: string; products: BuyoutProduct[]; period: DashboardPeriod; confirm: (message: string) => Promise<boolean>;
}) {
  const canWrite = role === "owner" || role === "manager";
  const [open, setOpen] = useState(false);
  const [group, setGroup] = useState<BuyoutGroup>("day");
  const [report, setReport] = useState<BuyoutReport | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [tick, setTick] = useState(0);
  const [editing, setEditing] = useState<BuyoutRecord | "new" | null>(null);
  const seq = useRef(0);

  useEffect(() => {
    if (!open) return;
    const mine = ++seq.current;
    const controller = new AbortController();
    setLoading(true); setError("");
    getSelfBuyouts(period, group, controller.signal)
      .then((data) => { if (mine === seq.current) setReport(data); })
      .catch((reason) => { if (mine === seq.current && !controller.signal.aborted) { setReport(null); setError(reason instanceof Error ? reason.message : "Не удалось загрузить журнал"); } })
      .finally(() => { if (mine === seq.current) setLoading(false); });
    return () => controller.abort();
  }, [open, period.id, period.start, period.end, group, tick]);

  const reload = () => setTick((n) => n + 1);
  const remove = async (record: BuyoutRecord) => {
    if (!(await confirm(`Удалить запись от ${ruDate(record.day)} (${record.vendor_code || record.nm_id}, ${record.units} шт, ${formatRub(record.buyout_amount_kop)})? Это действие нельзя отменить.`))) return;
    try { await deleteSelfBuyout(record.id); reload(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Не удалось удалить запись"); }
  };
  const exportCsv = () => {
    if (!report) return;
    const rows = report.records.map((r) => [r.day, r.vendor_code, r.nm_id, r.units, kopText(r.buyout_amount_kop), kopText(r.cashback_kop), kopText(r.net_cost_kop), r.format ?? "", r.note ?? ""]);
    void saveBlob(new Blob([buildCsv(["Дата", "Артикул продавца", "Артикул WB", "Шт", "Оплачено, ₽", "Кешбэк, ₽", "Чистые затраты, ₽", "Метка", "Примечание"], rows)], { type: "text/csv;charset=utf-8" }), `self-buyouts-${report.start}-${report.end}.csv`);
  };

  const cards: Array<[string, keyof BuyoutReport["deltas"], boolean]> = [["Штук", "units", false], ["Оплачено", "paid_kop", true], ["Кешбэк", "cashback_kop", true], ["Чистые затраты", "net_cost_kop", true]];
  return (
    <details className="cabinet-disclosure self-buyouts" onToggle={(event) => setOpen(event.currentTarget.open)}>
      <summary>Самовыкупы (учёт затрат)</summary>
      {!open ? null : <>
        <p className="sb-note">Ручной учёт затрат. Данные не отправляются в WB и не влияют на прибыль в других разделах.</p>
        {error ? <p className="operation-error" role="alert">{error} <button className="ghost-button" type="button" onClick={reload}>Повторить</button></p> : null}
        {loading && !report ? <p className="plan-empty" role="status">Загрузка журнала…</p> : null}
        {report ? <>
          <p className="sb-window" aria-live="polite">{ruDate(report.start)} – {ruDate(report.end)}{report.previous_window ? `; сравнение с ${ruDate(report.previous_window.start)} – ${ruDate(report.previous_window.end)}` : ""}{loading ? " · обновление…" : ""}</p>
          <div className="sb-cards">
            {cards.map(([label, key, money]) => {
              const d = report.deltas[key]; const view = deltaView(d, money);
              return <div className="sb-card" key={key}><span>{label}</span><strong>{money ? formatRub(d.value) : `${d.value.toLocaleString("ru-RU")} шт`}</strong><small aria-label={view.label}>{view.text}</small></div>;
            })}
          </div>
          <div className="sb-toolbar">
            <div role="group" aria-label="Группировка">{GROUPS.map(([id, label]) => <button key={id} type="button" className="ghost-button" aria-pressed={group === id} onClick={() => setGroup(id)}>{label}</button>)}</div>
            <div>
              <button className="ghost-button" type="button" onClick={exportCsv} disabled={!report.records.length}>Экспорт CSV</button>
              {canWrite ? <button className="secondary-button" type="button" onClick={() => setEditing("new")}>Добавить запись</button> : null}
            </div>
          </div>
          {!report.records.length ? <p className="plan-empty">За выбранный период записей нет.</p> : <>
            <div className="sb-scroll"><table aria-label="Затраты по периодам">
              <thead><tr><th scope="col">Период</th><th scope="col">Шт</th><th scope="col">Оплачено</th><th scope="col">Кешбэк</th><th scope="col">Чистые затраты</th></tr></thead>
              <tbody>{report.groups.map((g) => <tr key={g.start}><th scope="row">{groupLabel(g.start, group)}</th><td>{g.units.toLocaleString("ru-RU")}</td><td>{formatRub(g.paid_kop)}</td><td>{formatRub(g.cashback_kop)}</td><td>{formatRub(g.net_cost_kop)}</td></tr>)}</tbody>
            </table></div>
            <div className="sb-scroll"><table aria-label="Записи журнала">
              <thead><tr><th scope="col">Дата</th><th scope="col">Артикул</th><th scope="col">Шт</th><th scope="col">Оплачено</th><th scope="col">Кешбэк</th><th scope="col">Чистые затраты</th><th scope="col">Метка</th><th scope="col">Примечание</th>{canWrite ? <th scope="col">Действия</th> : null}</tr></thead>
              <tbody>{report.records.map((r) => <tr key={r.id}>
                <td>{ruDate(r.day)}</td><td>{r.vendor_code || "—"}<small> WB {r.nm_id}</small></td><td>{r.units.toLocaleString("ru-RU")}</td>
                <td>{formatRub(r.buyout_amount_kop)}</td><td>{formatRub(r.cashback_kop)}</td><td>{formatRub(r.net_cost_kop)}</td><td>{r.format || "—"}</td><td>{r.note || "—"}</td>
                {canWrite ? <td className="sb-actions">
                  <button className="ghost-button" type="button" aria-label={`Изменить запись от ${ruDate(r.day)}, ${r.vendor_code || r.nm_id}`} onClick={() => setEditing(r)}>Изменить</button>
                  <button className="ghost-button" type="button" aria-label={`Удалить запись от ${ruDate(r.day)}, ${r.vendor_code || r.nm_id}`} onClick={() => void remove(r)}>Удалить</button>
                </td> : null}
              </tr>)}</tbody>
            </table></div>
            {report.truncated ? <p className="sb-note">Показаны 500 последних записей; итоги выше посчитаны по всем записям периода.</p> : null}
          </>}
        </> : null}
        {canWrite && editing ? <BuyoutForm key={editing === "new" ? "new" : editing.id} record={editing === "new" ? null : editing} products={products}
          onClose={() => setEditing(null)} onSaved={() => { setEditing(null); reload(); }} /> : null}
        {!canWrite && report ? <p className="sb-note">Доступ только для чтения.</p> : null}
      </>}
    </details>
  );
}

function BuyoutForm({ record, products, onClose, onSaved }: { record: BuyoutRecord | null; products: BuyoutProduct[]; onClose: () => void; onSaved: () => void }) {
  const [form, setForm] = useState<FormState>(() => record ? {
    day: record.day, nm: String(record.nm_id), units: String(record.units), paid: kopText(record.buyout_amount_kop),
    cashback: record.cashback_kop ? kopText(record.cashback_kop) : "", format: record.format ?? "", note: record.note ?? "",
  } : emptyForm());
  const [fields, setFields] = useState<Fields>({});
  const [formError, setFormError] = useState("");
  const [busy, setBusy] = useState(false);
  const [search, setSearch] = useState("");
  const set = (key: keyof FormState) => (value: string) => setForm((f) => ({ ...f, [key]: value }));

  const options = useMemo(() => {
    const q = search.trim().toLowerCase();
    const found = products.filter((p) => !q || `${p.article} ${p.nmId} ${p.product}`.toLowerCase().includes(q)).slice(0, 50);
    if (form.nm && !found.some((p) => p.nmId === form.nm)) {
      const current = products.find((p) => p.nmId === form.nm);
      found.unshift(current ?? { nmId: form.nm, article: record?.vendor_code ?? "", product: "" });
    }
    return found;
  }, [products, search, form.nm, record]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const result = validate(form);
    setFields(result.fields); setFormError("");
    if (!result.payload) return;
    setBusy(true);
    try {
      if (record) await updateSelfBuyout(record.id, result.payload); else await createSelfBuyout(result.payload);
      onSaved();
    } catch (reason) {
      if (reason instanceof BuyoutError && Object.keys(reason.fields).length) {
        const mapped: Fields = {};
        for (const [key, message] of Object.entries(reason.fields)) mapped[SERVER_FIELD[key] ?? "note"] = message;
        setFields(mapped);
      } else setFormError(reason instanceof Error ? reason.message : "Не удалось сохранить запись");
    } finally { setBusy(false); }
  };

  const err = (key: keyof FormState) => fields[key] ? <span className="sb-error" id={`sb-err-${key}`} role="alert">{fields[key]}</span> : null;
  const aria = (key: keyof FormState) => ({ "aria-invalid": fields[key] ? true : undefined, "aria-describedby": fields[key] ? `sb-err-${key}` : undefined });
  const today = localToday();
  return (
    <form className="sb-form" onSubmit={(event) => void submit(event)} noValidate aria-label={record ? "Изменить запись" : "Новая запись"}>
      <strong>{record ? "Изменить запись" : "Новая запись"}</strong>
      <label>Дата<input type="date" value={form.day} min={shiftDays(today, -400)} max={shiftDays(today, 1)} onChange={(e) => set("day")(e.target.value)} {...aria("day")} />{err("day")}</label>
      {products.length ? <>
        <label>Поиск товара<input type="search" value={search} placeholder="Артикул или название" onChange={(e) => setSearch(e.target.value)} /></label>
        <label>Товар<select value={form.nm} onChange={(e) => set("nm")(e.target.value)} {...aria("nm")}>
          <option value="">— выберите товар —</option>
          {options.map((p) => <option key={p.nmId} value={p.nmId}>{p.article || "без артикула"} · WB {p.nmId}{p.product ? ` · ${p.product}` : ""}</option>)}
        </select>{err("nm")}</label>
      </> : <label>Артикул WB (nmID)<input inputMode="numeric" value={form.nm} onChange={(e) => set("nm")(e.target.value)} {...aria("nm")} />{err("nm")}</label>}
      <label>Количество, шт<input inputMode="numeric" value={form.units} onChange={(e) => set("units")(e.target.value)} {...aria("units")} />{err("units")}</label>
      <label>Оплачено, ₽<input inputMode="decimal" value={form.paid} placeholder="0,00" onChange={(e) => set("paid")(e.target.value)} {...aria("paid")} />{err("paid")}</label>
      <label>Кешбэк, ₽ (необязательно)<input inputMode="decimal" value={form.cashback} placeholder="0,00" onChange={(e) => set("cashback")(e.target.value)} {...aria("cashback")} />{err("cashback")}</label>
      <label>Метка (необязательно)<input maxLength={60} value={form.format} onChange={(e) => set("format")(e.target.value)} {...aria("format")} />{err("format")}</label>
      <label>Примечание (необязательно)<input maxLength={300} value={form.note} onChange={(e) => set("note")(e.target.value)} {...aria("note")} />{err("note")}</label>
      {formError ? <p className="operation-error" role="alert">{formError}</p> : null}
      <div className="sb-form-actions">
        <button className="primary-button" type="submit" disabled={busy}>{busy ? "Сохранение…" : "Сохранить"}</button>
        <button className="ghost-button" type="button" onClick={onClose} disabled={busy}>Отмена</button>
      </div>
    </form>
  );
}
