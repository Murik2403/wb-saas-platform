import { useMemo, useRef, useState } from "react";
import { AlertTriangle, CheckCircle2, ClipboardList, LoaderCircle, RefreshCw } from "lucide-react";
import { confirmSupplyPlan, previewSupplyPlan, SupplyPlanHttpError, type SupplyPlanAllocation, type SupplyPlanEdit, type SupplyPlanPreview } from "./data-source";

type Props = { readOnly: boolean };
const localDate = (value = new Date()) => `${value.getFullYear()}-${String(value.getMonth() + 1).padStart(2, "0")}-${String(value.getDate()).padStart(2, "0")}`;
const today = () => localDate();
const inTwoWeeks = () => { const value = new Date(); value.setDate(value.getDate() + 14); return localDate(value); };
const addDays = (base: string, days: number) => { const value = new Date(`${base}T12:00:00`); value.setDate(value.getDate() + days); return localDate(value); };
const keyOf = (task: string, variant: number) => JSON.stringify([task, variant]);
const warningText = (warning: SupplyPlanPreview["warnings"][number]) => {
  if (warning.code === "fallback_to_base_bom") return "Использована общая спецификация товара.";
  if (warning.code === "variant_demand_aggregated") return "Состав вариантов совпадает; объём объединён по спецификации.";
  if (warning.code === "open_order_eta_risk") return `Поставка ${String(warning.order_id ?? "")} не покрывает потребность к сроку: ожидаемая дата ${String(warning.expected_date ?? "не указана")}.`;
  return "В расчёте есть предупреждение: проверьте исходные данные.";
};

export function SupplyPlanning({ readOnly, onRefresh }: Props & { onRefresh: () => Promise<void> }) {
  const [startDate, setStartDate] = useState(today);
  const [endDate, setEndDate] = useState(inTwoWeeks);
  const [preview, setPreview] = useState<SupplyPlanPreview | null>(null);
  const [allocations, setAllocations] = useState<Record<string, number>>({});
  const [edits, setEdits] = useState<Record<string, SupplyPlanEdit>>({});
  const [busy, setBusy] = useState<"preview" | "confirm" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [success, setSuccess] = useState<string | null>(null);
  const [etaHints, setEtaHints] = useState<Record<string, string>>({});
  const [pending, setPending] = useState<{ signature: string; body: Parameters<typeof confirmSupplyPlan>[0] } | null>(null);
  const [previewRequestSignature, setPreviewRequestSignature] = useState<string | null>(null);
  const submitLock = useRef(false);
  const allocationPayload = useMemo<SupplyPlanAllocation[]>(() => Object.entries(allocations)
    .filter(([, units]) => units > 0)
    .map(([key, units]) => { const [task_key, chrt_id] = JSON.parse(key) as [string, number]; return { task_key, chrt_id, units }; }), [allocations]);
  const currentPreviewRequestSignature = JSON.stringify({ startDate, endDate, allocationPayload });
  const runPreview = async (preserveSuccess = false) => {
    if (pending || busy) return;
    setBusy("preview"); setError(null); if (!preserveSuccess) setSuccess(null);
    try {
      const result = await previewSupplyPlan({ start_date: startDate, end_date: endDate, variant_allocations: allocationPayload });
      setPreview(result);
      setPreviewRequestSignature(currentPreviewRequestSignature);
      setEdits(Object.fromEntries(result.lines.filter((line) => line.suggested_purchase_qty > 0).map((line) => [line.material_key, {
        material_key: line.material_key, unit: line.unit, quantity: line.suggested_purchase_qty,
        supplier_id: null, expected_date: "",
      }])));
      setEtaHints({});
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Предпросмотр не загрузился"); }
    finally { setBusy(null); }
  };
  const updateEdit = (line: SupplyPlanPreview["lines"][number], patch: Partial<SupplyPlanEdit>) => {
    setEdits((current) => {
      const existing = current[line.material_key] ?? { material_key: line.material_key, unit: line.unit, quantity: line.suggested_purchase_qty, supplier_id: null, expected_date: "" };
      return { ...current, [line.material_key]: { ...existing, ...patch } };
    });
  };
  const linesToOrder = preview?.lines.filter((line) => line.suggested_purchase_qty > 0) ?? [];
  const draftEdits = linesToOrder.map((line) => edits[line.material_key]).filter(Boolean);
  const draftSignature = JSON.stringify({ preview_fingerprint: preview?.preview_fingerprint, startDate, endDate, allocationPayload, edits: draftEdits });
  const requestBody = (): Parameters<typeof confirmSupplyPlan>[0] => ({
    confirmation_key: crypto.randomUUID(), preview_fingerprint: preview!.preview_fingerprint,
    start_date: startDate, end_date: endDate, variant_allocations: allocationPayload, edits: draftEdits,
  });
  const submit = async () => {
    if (!preview || !canConfirm || readOnly || busy || submitLock.current) return;
    submitLock.current = true;
    let prepared = pending?.signature === draftSignature ? pending : null;
    if (!prepared) {
      const orderGroupCount = new Set(linesToOrder.map((line) => {
        const edit = edits[line.material_key];
        const supplier = line.supplier_candidates.find((candidate) => candidate.id === edit?.supplier_id);
        return JSON.stringify([edit?.supplier_id, edit?.expected_date, supplier?.currency ?? "RUB"]);
      })).size;
      if (!window.confirm(`Создать ${orderGroupCount} внутренних заявок для ${linesToOrder.length} позиций? Поставщику они не отправляются.`)) { submitLock.current = false; return; }
      prepared = { signature: draftSignature, body: requestBody() };
      setPending(prepared);
    }
    setBusy("confirm"); setError(null); setSuccess(null);
    try {
      const result = await confirmSupplyPlan({ ...prepared.body, edits: prepared.body.edits.map((edit) => ({ ...edit, supplier_id: edit.supplier_id! })) });
      setPending(null);
      await onRefresh().catch(() => undefined);
      setPreview(null);
      setSuccess(`Созданы внутренние заявки ${result.order_numbers.join(", ")} со статусом «Запланировано». Заказ поставщику не отправлялся.`);
    } catch (caught) {
      const message = caught instanceof Error ? caught.message : "Не удалось создать внутреннюю заявку";
      if (caught instanceof SupplyPlanHttpError && caught.status < 500) setPending(null);
      if (caught instanceof SupplyPlanHttpError && caught.code === "stale_preview") setPreviewRequestSignature(null);
      setError(caught instanceof SupplyPlanHttpError && caught.code === "stale_preview" ? "Данные изменились после расчёта. Обновите предпросмотр и проверьте количества заново." : message);
    } finally { setBusy(null); submitLock.current = false; }
  };
  const canConfirm = !!preview && previewRequestSignature === currentPreviewRequestSignature && preview.blockers.length === 0 && linesToOrder.length > 0 && linesToOrder.every((line) => {
    const edit = edits[line.material_key];
    return edit?.supplier_id !== null && edit?.supplier_id !== undefined && !!edit?.expected_date && edit.quantity >= line.suggested_purchase_qty
      && (line.tracking_mode !== "packaged" || Number.isInteger(edit.quantity));
  });

  return <section className="work-section supply-planning" aria-labelledby="supply-planning-title">
    <div className="section-header"><div><span className="section-kicker">План закупки · расчёт по незавершённым производственным заданиям</span><h2 id="supply-planning-title">Потребность в сырье</h2></div><ClipboardList size={20} aria-hidden="true" /></div>
    <p className="supply-plan-note">Предпросмотр только читает данные. Создание внутренней заявки происходит отдельно; поставщику она не отправляется.</p>
    <div className="supply-plan-controls">
      <label>С даты<input type="date" value={startDate} disabled={pending !== null} onChange={(event) => setStartDate(event.target.value)} /></label>
      <label>По дату<input type="date" value={endDate} disabled={pending !== null} onChange={(event) => setEndDate(event.target.value)} /></label>
      <button className="secondary-button" type="button" onClick={() => void runPreview()} disabled={busy !== null || pending !== null || endDate < startDate}>
        {busy === "preview" ? <LoaderCircle size={16} /> : <RefreshCw size={16} />}{preview ? "Обновить расчёт" : "Рассчитать потребность"}
      </button>
    </div>
    {error ? <p className="operation-error" role="alert">{error}</p> : null}
    {success ? <p className="supply-plan-success" role="status"><CheckCircle2 size={16} />{success}</p> : null}
    {preview ? <>
      {previewRequestSignature !== currentPreviewRequestSignature ? <p className="supply-plan-blockers" role="status">Период или распределение изменились. Предпросмотр устарел — пересчитайте его перед подтверждением.</p> : null}
      {preview.variant_demands.length ? <div className="supply-variant-allocation"><h3>Распределите план по вариантам</h3><p>Для каждого задания сумма размеров должна точно совпадать с плановым остатком.</p>
        {preview.variant_demands.map((demand) => <fieldset key={demand.task_key}><legend>Задание {demand.task_key} · SKU {demand.nm_id} · остаток {demand.units} шт.</legend><div className="supply-plan-controls">{demand.chrt_ids.map((chrt) => <label key={chrt}>Вариант {chrt}<input type="number" min="0" step="1" disabled={pending !== null} value={allocations[keyOf(demand.task_key, chrt)] ?? ""} onChange={(event) => setAllocations((current) => ({ ...current, [keyOf(demand.task_key, chrt)]: Number(event.target.value) }))} /></label>)}</div></fieldset>)}
        <button className="secondary-button" type="button" disabled={busy !== null || pending !== null} onClick={() => void runPreview()}>Пересчитать с распределением</button>
      </div> : null}
      {preview.blockers.length ? <div className="supply-plan-blockers" role="alert"><strong><AlertTriangle size={16} />Подтверждение пока недоступно</strong>{preview.blockers.map((blocker, index) => <p key={`${blocker.code}-${index}`}>{blocker.message ?? blocker.code}{blocker.material_key ? ` · ${blocker.material_key}` : ""}</p>)}</div> : null}
      {preview.warnings.length ? <ul className="supply-plan-warnings">{preview.warnings.map((warning, index) => <li key={`${String(warning.code)}-${index}`}>{warningText(warning)}</li>)}</ul> : null}
      {preview.intermediate_allocations?.length ? <div className="supply-plan-table-wrap"><h3>Полуфабрикаты по этапам</h3><p>Остаток FIFO и запланированный выпуск распределены между заданиями один раз, с учётом дат. Недостающие этапы раскрыты до сырья.</p><table className="supply-plan-table"><thead><tr><th>Задание / вариант</th><th>Нужно</th><th>Покрыто</th><th>Произвести</th><th>Срок / спецификация</th></tr></thead><tbody>{preview.intermediate_allocations.map((stage, index) => <tr key={`${stage.task_key}-${index}`}><td>{stage.task_key}<small>WB {stage.nm_id} · вариант {stage.chrt_id}</small></td><td>{stage.required_units} шт.</td><td>{stage.covered_units} шт.</td><td>{stage.produce_units} шт.</td><td>{stage.due_date}<small>Спецификация #{stage.bom_version_id}</small></td></tr>)}</tbody></table></div> : null}
      {preview.lines.length ? <div className="supply-plan-table-wrap"><table className="supply-plan-table"><thead><tr><th>Материал / BOM</th><th>Нужно</th><th>Остаток / к сроку</th><th>Дефицит</th><th>Заказать</th><th>Поставщик / ожидаемая дата</th></tr></thead><tbody>
        {preview.lines.map((line) => { const edit = edits[line.material_key] ?? { material_key: line.material_key, unit: line.unit, quantity: line.suggested_purchase_qty, supplier_id: null, expected_date: "" }; return <tr key={line.material_key}><td><strong>{line.material_name}</strong><small>{line.material_key} · {line.unit} · BOM v{line.source_refs.map((ref) => ref.bom_version).join(", ")}</small>{line.source_refs.filter((ref) => (ref.route?.length ?? 0) > 1).map((ref, index) => <small key={index}>Маршрут: {ref.route!.map((step) => `WB ${step.nm_id}/${step.chrt_id} · v${step.bom_version}`).join(" → ")}</small>)}{line.fallback_to_base_bom ? <small>Общая спецификация</small> : null}</td><td>{line.required_base_qty} {line.unit}<small>до {line.due_date}</small></td><td>{line.physical_available_base_qty === null ? "неизвестно" : `${line.physical_available_base_qty} ${line.unit}`}<small>Ожидается к сроку: {line.open_order_covered_base_qty} {line.unit}</small></td><td>{line.shortage_base_qty} {line.unit}</td><td><label className="supply-plan-inline-field"><span className="sr-only">Количество заказа {line.material_name}</span><input aria-label={`Количество заказа ${line.material_name}`} type="number" min={line.suggested_purchase_qty} step={line.tracking_mode === "packaged" ? 1 : 0.001} disabled={pending !== null} value={edit.quantity} onChange={(event) => updateEdit(line, { quantity: Number(event.target.value) })} />{line.tracking_mode === "packaged" ? ` рул. × ${line.pack_size_base_qty} ${line.unit}` : ` ${line.unit}`}</label></td><td><select aria-label={`Поставщик ${line.material_name}`} value={edit.supplier_id ?? ""} disabled={pending !== null} onChange={(event) => { const id = event.target.value ? Number(event.target.value) : null; const supplier = line.supplier_candidates.find((item) => item.id === id); updateEdit(line, { supplier_id: id, expected_date: supplier ? addDays(today(), supplier.lead_time_days) : "" }); setEtaHints((current) => ({ ...current, [line.material_key]: supplier ? `Оценка по сроку поставщика: ${supplier.lead_time_days} дн.; дата не подтверждена поставщиком.` : "" })); }}><option value="">Выберите поставщика</option>{line.supplier_candidates.map((supplier) => <option key={supplier.id} value={supplier.id}>{supplier.name} · срок {supplier.lead_time_days} дн.</option>)}</select><input aria-label={`Ожидаемая дата поставки ${line.material_name}`} disabled={pending !== null} type="date" value={edit.expected_date} onChange={(event) => { updateEdit(line, { expected_date: event.target.value }); setEtaHints((current) => ({ ...current, [line.material_key]: "Дата указана вручную; подтверждение срока поставщиком не проверено." })); }} />{etaHints[line.material_key] ? <small>{etaHints[line.material_key]}</small> : null}{edit.expected_date && edit.expected_date > line.due_date ? <small role="status">Поставка после срока потребности: производство может задержаться.</small> : null}</td></tr>; })}
      </tbody></table></div> : <p className="plan-empty">На выбранный период нет потребности в сырье по открытым производственным заданиям.</p>}
      <div className="supply-plan-footer"><span>Цены и доставка не заполнены автоматически. Проверьте черновик перед сохранением.{pending ? " Ответ сервера не получен: повтор отправит тот же ключ и тот же черновик." : ""}</span><button className="primary-button" type="button" disabled={!canConfirm || readOnly || busy !== null} onClick={() => void submit()}>{busy === "confirm" ? "Создаём заявку…" : pending ? "Повторить подтверждение" : "Создать внутреннюю заявку"}</button></div>
      {readOnly ? <p className="supply-plan-note">Режим просмотра: внутреннюю заявку может подтвердить только владелец или менеджер.</p> : null}
    </> : null}
  </section>;
}
