import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { API_BASE_URL, apiHeaders } from "./data-source";
import "./ManufacturingCosts.css";

type Material = { material_name: string; unit: string };
type Layer = Material & { id: number; remaining_quantity: number; unit_cost_rub: number };
type Product = { nm_id: number; product_name: string; supplier_article?: string };
type Equipment = { id: number; name: string; resource_unit: "units" | "hours"; resource_quantity: number; consumed_resource: number; rate_per_resource_unit: number };
type Bom = { id: number; nm_id: number; version: number; note?: string; materials?: Array<{ material_name: string; quantity_per_unit: number; unit: string }>; operations?: Array<{ name: string; rate_per_unit: number }>; packaging_per_unit?: number; other_per_unit?: number };
type RecordRow = Record<string, unknown>;
export type ManufacturingCostsData = {
  materials: Material[]; material_layers: Layer[]; products: Product[];
  boms: Bom[]; equipment: Equipment[]; piecework: RecordRow[]; defects: RecordRow[]; releases: RecordRow[];
  allowed_actions?: string[];
};
type MaterialInput = { material_name: string; quantity: string };
type OperationInput = { name: string; rate: string; equipment_id: string; usage: string };
const endpoint = "/api/v1/manufacturing-costs";
const rub = (value: unknown) => Number(value ?? 0).toLocaleString("ru-RU", { style: "currency", currency: "RUB", minimumFractionDigits: 2 });
const today = () => { const date = new Date(); return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`; };
const eventKey = () => crypto.randomUUID();

/** Decimal fields accept either locale separator, but never silently round money. */
export function manufacturingNumber(value: string, label: string, options: { money?: boolean; positive?: boolean; integer?: boolean } = {}) {
  const normalized = value.trim().replace(",", ".");
  if (!/^\d+(?:\.\d+)?$/.test(normalized)) throw new Error(`${label}: введите число без разделителей тысяч.`);
  if (options.money && (normalized.split(".")[1]?.length ?? 0) > 2) throw new Error(`${label}: укажите не более двух знаков после запятой.`);
  const number = Number(normalized);
  if (!Number.isFinite(number) || number > Number.MAX_SAFE_INTEGER || (options.positive && number <= 0) || (options.integer && !Number.isSafeInteger(number))) {
    throw new Error(`${label}: требуется ${options.integer ? "целое " : ""}${options.positive ? "положительное " : "неотрицательное "}число.`);
  }
  return number;
}

async function request<T>(path = "", body?: unknown, signal?: AbortSignal): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${endpoint}${path}`, {
    method: body === undefined ? "GET" : "POST", headers: apiHeaders(body !== undefined),
    credentials: "same-origin", body: body === undefined ? undefined : JSON.stringify(body), signal,
  });
  if (!response.ok) {
    const payload = await response.json().catch(() => null) as { detail?: unknown } | null;
    let detail = "";
    if (typeof payload?.detail === "string") detail = payload.detail;
    else if (Array.isArray(payload?.detail)) detail = payload.detail.map((item) => typeof item?.msg === "string" ? item.msg : "Некорректные данные").join("; ");
    throw new Error(detail || (response.status === 404 ? "Производственный учёт пока недоступен на сервере." : `Не удалось выполнить запрос (${response.status}).`));
  }
  return await response.json() as T;
}

function Field({ label, children }: { label: string; children: ReactNode }) {
  return <label className="mfg-field"><span>{label}</span>{children}</label>;
}
function Decimal({ label, value, onChange }: { label: string; value: string; onChange: (value: string) => void }) {
  return <Field label={label}><input required inputMode="decimal" value={value} onChange={(event) => onChange(event.target.value)} /></Field>;
}
function ProductSelect({ products, value, onChange, label }: { products: Product[]; value: string; onChange: (value: string) => void; label: string }) {
  return <Field label={label}><select required value={value} onChange={(event) => onChange(event.target.value)}><option value="">Выберите товар</option>{products.map((item) => <option key={item.nm_id} value={item.nm_id}>{item.product_name || `WB ${item.nm_id}`} · {item.nm_id}</option>)}</select></Field>;
}

export function ManufacturingCosts({ onChanged, canEdit = true }: { onChanged?: () => void; canEdit?: boolean } = {}) {
  const [data, setData] = useState<ManufacturingCostsData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [busy, setBusy] = useState(false);
  const writeLock = useRef(false);
  const [reloadKey, setReloadKey] = useState(0);
  const [bomProduct, setBomProduct] = useState("");
  const [components, setComponents] = useState<MaterialInput[]>([{ material_name: "", quantity: "1" }]);
  const [operations, setOperations] = useState<OperationInput[]>([]);
  const [packaging, setPackaging] = useState("0");
  const [other, setOther] = useState("0");
  const [bomNote, setBomNote] = useState("");
  const [scrap, setScrap] = useState({ layer: "", quantity: "", reason: "", date: today(), key: eventKey() });
  const [labor, setLabor] = useState({ source: "", product: "", operation: "", worker: "", quantity: "", rate: "", date: today(), key: eventKey() });
  const [equipment, setEquipment] = useState({ name: "", cost: "", salvage: "0", resource: "", unit: "hours" });
  const [release, setRelease] = useState({ source: "", product: "", bom: "", quantity: "", date: today() });
  const load = useCallback(async (signal?: AbortSignal) => {
    const result = await request<ManufacturingCostsData>("", undefined, signal);
    if (![result.materials, result.material_layers, result.products, result.boms, result.equipment, result.piecework, result.defects, result.releases].every(Array.isArray)) throw new Error("Сервер вернул неполные данные производственного учёта.");
    setData(result);
  }, []);
  useEffect(() => {
    const controller = new AbortController();
    setLoading(true); setError("");
    load(controller.signal).catch((reason) => { if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Ошибка загрузки"); }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [load, reloadKey]);

  const permitted = (action: string) => canEdit && (data?.allowed_actions === undefined || data.allowed_actions.includes(action));
  async function save(event: { preventDefault: () => void }, path: string, makeBody: () => unknown, message: string, clear: () => void) {
    event.preventDefault();
    const action = path.startsWith("/movements/") ? "reverse" : path.slice(1);
    if (writeLock.current || !permitted(action)) return;
    setError(""); setSuccess("");
    let body: unknown;
    try { body = makeBody(); } catch (reason) { setError(reason instanceof Error ? reason.message : "Проверьте поля"); return; }
    writeLock.current = true; setBusy(true);
    try {
      await request(path, body);
      setSuccess(message); clear();
      // A parent refresh is informational: it must never turn a committed write into a retry.
      try { onChanged?.(); } catch { /* The local refresh below remains authoritative. */ }
      try { await load(); } catch { setError("Запись сохранена, но список не обновился. Нажмите «Обновить данные»; повторно отправлять форму не нужно."); }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Сохранение не удалось. Проверьте данные и повторите."); }
    finally { writeLock.current = false; setBusy(false); }
  }
  const number = manufacturingNumber;
  const updateComponent = (index: number, values: Partial<MaterialInput>) => setComponents((items) => items.map((item, i) => i === index ? { ...item, ...values } : item));
  const updateOperation = (index: number, values: Partial<OperationInput>) => setOperations((items) => items.map((item, i) => i === index ? { ...item, ...values } : item));
  const activeLayer = data?.material_layers.find((item) => String(item.id) === scrap.layer);
  const disabled = busy || loading || !canEdit;
  return <section className="manufacturing-costs" aria-labelledby="mfg-title" aria-busy={busy || loading}>
    <div className="mfg-heading"><div><h2 id="mfg-title">Состав изделия и затраты производства</h2><p>Материалы, работа и износ оборудования входят в стоимость выпуска. Списанный брак учитывается отдельно.</p><p>Это стоимость выпуска и запасов. Финансовый отчёт пока использует себестоимость из настроек товара; выпуск сам по себе не является расходом от продаж.</p></div><button type="button" className="ghost-button" disabled={busy || loading} onClick={() => setReloadKey((value) => value + 1)}>Обновить данные</button></div>
    {(!canEdit || data?.allowed_actions?.length === 0) && <p>У вашей роли нет прав на изменение производственного учёта. Данные доступны для просмотра.</p>}
    {canEdit && data?.allowed_actions && data.allowed_actions.length > 0 && data.allowed_actions.length < 6 && <p>Доступные действия ограничены вашей ролью. Остальные формы доступны для просмотра.</p>}
    {loading && <p role="status">Загружаем производственный учёт…</p>}
    {error && <p role="alert" className="mfg-error">{error}</p>}
    {success && <p role="status" className="mfg-success">{success}</p>}
    {data && <div className="mfg-panels">
      <form onSubmit={(event) => void save(event, "/boms", () => {
        const names = components.map((item) => item.material_name);
        if (new Set(names).size !== names.length) throw new Error("Каждый материал добавьте в состав один раз.");
        return {
          nm_id: number(bomProduct, "Товар", { positive: true, integer: true }),
          materials: components.map((item) => { const material = data.materials.find((row) => row.material_name === item.material_name); if (!material) throw new Error("Выберите материал из справочника."); return { material_name: material.material_name, unit: material.unit, quantity_per_unit: number(item.quantity, `Расход ${material.material_name}`, { positive: true }) }; }),
          operations: operations.map((item) => { if (!item.name.trim()) throw new Error("Укажите название операции."); return { name: item.name.trim(), rate_per_unit: number(item.rate, "Расценка", { money: true }), ...(item.equipment_id ? { equipment_id: number(item.equipment_id, "Оборудование", { integer: true, positive: true }), equipment_usage_per_unit: number(item.usage, "Расход ресурса", { positive: true }) } : {}) }; }),
          packaging_per_unit: number(packaging, "Упаковка", { money: true }), other_per_unit: number(other, "Прочие затраты", { money: true }), note: bomNote.trim(),
        };
      }, "Новая версия состава изделия сохранена.", () => setBomNote(""))}>
        <fieldset disabled={disabled || !permitted("boms") || !data.products.length || !data.materials.length}><legend>Состав изделия — новая версия</legend>
          <p>Расход указывается на одну продаваемую единицу. Сохранение создаёт новую версию; прежние выпуски сохраняют свою себестоимость.</p>
          {!data.products.length || !data.materials.length ? <p>Сначала добавьте товары и материалы в справочники.</p> : null}
          <ProductSelect label="Товар для состава изделия" products={data.products} value={bomProduct} onChange={setBomProduct} />
          {components.map((item, index) => { const material = data.materials.find((row) => row.material_name === item.material_name); return <div className="mfg-row" key={index}>
            <Field label={`Материал ${index + 1}`}><select required value={item.material_name} onChange={(event) => updateComponent(index, { material_name: event.target.value })}><option value="">Выберите материал</option>{data.materials.map((row) => <option key={row.material_name} value={row.material_name}>{row.material_name} ({row.unit})</option>)}</select></Field>
            <Decimal label={`Расход материала ${index + 1}${material ? `, ${material.unit}` : ""}`} value={item.quantity} onChange={(quantity) => updateComponent(index, { quantity })} />
            <button type="button" disabled={components.length === 1} onClick={() => setComponents((items) => items.filter((_, i) => i !== index))} aria-label={`Удалить материал ${index + 1}`}>Удалить</button>
          </div>; })}
          <button type="button" onClick={() => setComponents((items) => [...items, { material_name: "", quantity: "1" }])}>Добавить материал</button>
          <h4>Операции и оборудование</h4><p>Плановая расценка применяется, если по заданию не внесена фактическая сдельная работа. Износ рассчитывается по использованному ресурсу.</p>
          {operations.map((item, index) => { const asset = data.equipment.find((row) => String(row.id) === item.equipment_id); return <div className="mfg-operation" key={index}>
            <Field label={`Операция ${index + 1}`}><input required value={item.name} onChange={(event) => updateOperation(index, { name: event.target.value })} /></Field>
            <Decimal label={`Расценка операции ${index + 1}, ₽/ед.`} value={item.rate} onChange={(rate) => updateOperation(index, { rate })} />
            <Field label={`Оборудование операции ${index + 1}`}><select value={item.equipment_id} onChange={(event) => updateOperation(index, { equipment_id: event.target.value })}><option value="">Без оборудования</option>{data.equipment.map((row) => <option key={row.id} value={row.id}>{row.name}</option>)}</select></Field>
            {asset && <Decimal label={`Ресурс операции ${index + 1}, ${asset.resource_unit === "hours" ? "ч" : "ед."}/изделие`} value={item.usage} onChange={(usage) => updateOperation(index, { usage })} />}
            <button type="button" onClick={() => setOperations((items) => items.filter((_, i) => i !== index))} aria-label={`Удалить операцию ${index + 1}`}>Удалить операцию</button>
          </div>; })}
          <button type="button" onClick={() => setOperations((items) => [...items, { name: "", rate: "0", equipment_id: "", usage: "1" }])}>Добавить операцию</button>
          <div className="mfg-row"><Decimal label="Упаковка, ₽/ед." value={packaging} onChange={setPackaging} /><Decimal label="Прочие затраты, ₽/ед." value={other} onChange={setOther} /></div>
          <Field label="Примечание к версии"><input value={bomNote} onChange={(event) => setBomNote(event.target.value)} /></Field>
          <button className="primary-button" type="submit">Сохранить новую версию</button>
          {data.boms.filter((item) => !bomProduct || item.nm_id === Number(bomProduct)).map((item) => <details key={item.id}><summary>WB {item.nm_id} · версия {item.version}{item.note ? ` · ${item.note}` : ""}</summary><ul>{item.materials?.map((material) => <li key={material.material_name}>{material.material_name}: {material.quantity_per_unit} {material.unit}/изделие</li>)}{item.operations?.map((operation, index) => <li key={index}>{operation.name}: {rub(operation.rate_per_unit)}/изделие</li>)}</ul><p>Упаковка: {rub(item.packaging_per_unit)}/изделие. Прочие: {rub(item.other_per_unit)}/изделие.</p></details>)}
        </fieldset>
      </form>

      <form onSubmit={(event) => void save(event, "/equipment", () => {
        const cost = number(equipment.cost, "Стоимость оборудования", { money: true, positive: true });
        const salvage = number(equipment.salvage, "Остаточная стоимость", { money: true });
        if (salvage > cost) throw new Error("Остаточная стоимость не может превышать стоимость приобретения.");
        if (!equipment.name.trim()) throw new Error("Укажите название оборудования.");
        return { name: equipment.name.trim(), acquisition_cost_rub: cost, salvage_value_rub: salvage, resource_quantity: number(equipment.resource, "Ресурс оборудования", { positive: true }), resource_unit: equipment.unit };
      }, "Оборудование добавлено. Выберите его в операции состава изделия.", () => setEquipment({ name: "", cost: "", salvage: "0", resource: "", unit: "hours" }))}>
        <fieldset disabled={disabled || !permitted("equipment")}><legend>Оборудование и амортизация</legend><p>Стоимость износа = (приобретение − остаточная стоимость) / ресурс × использованный ресурс. Начисляется при выпуске, когда оборудование указано в составе изделия.</p>
          <Field label="Название оборудования"><input required value={equipment.name} onChange={(event) => setEquipment({ ...equipment, name: event.target.value })} /></Field>
          <Decimal label="Стоимость приобретения, ₽" value={equipment.cost} onChange={(cost) => setEquipment({ ...equipment, cost })} />
          <Decimal label="Остаточная стоимость после выработки ресурса, ₽" value={equipment.salvage} onChange={(salvage) => setEquipment({ ...equipment, salvage })} />
          <Field label="Единица ресурса оборудования"><select value={equipment.unit} onChange={(event) => setEquipment({ ...equipment, unit: event.target.value })}><option value="hours">Часы</option><option value="units">Единицы</option></select></Field>
          <Decimal label="Полный ресурс оборудования" value={equipment.resource} onChange={(resource) => setEquipment({ ...equipment, resource })} />
          <button className="primary-button" type="submit">Добавить оборудование</button>
          <ul>{data.equipment.map((item) => <li key={item.id}>{item.name}: {rub(item.rate_per_resource_unit)}/{item.resource_unit === "hours" ? "ч" : "ед."}; использовано {item.consumed_resource ?? 0} из {item.resource_quantity}</li>)}</ul>
        </fieldset>
      </form>

      <form onSubmit={(event) => void save(event, "/piecework", () => {
        if (!labor.source.trim() || !labor.operation.trim() || !labor.worker.trim()) throw new Error("Укажите задание, операцию и исполнителя.");
        return { source_key: labor.source.trim(), nm_id: number(labor.product, "Товар", { positive: true, integer: true }), operation_name: labor.operation.trim(), worker: labor.worker.trim(), quantity: number(labor.quantity, "Объём работы", { positive: true }), rate_per_unit: number(labor.rate, "Расценка работы", { money: true }), work_date: labor.date, event_key: labor.key };
      }, "Сдельная работа сохранена и будет включена в выпуск этого задания.", () => setLabor((item) => ({ ...item, quantity: "", key: eventKey() })))}>
        <fieldset disabled={disabled || !permitted("piecework") || !data.products.length}><legend>Сдельная работа по заданию</legend><p>Внесите все выполненные операции до выпуска. Код задания должен совпадать с кодом в форме выпуска ниже.</p>
          <Field label="Код задания для работы"><input required value={labor.source} onChange={(event) => setLabor({ ...labor, source: event.target.value })} /></Field>
          <ProductSelect label="Товар для сдельной работы" products={data.products} value={labor.product} onChange={(product) => setLabor({ ...labor, product })} />
          <Field label="Название выполненной операции"><input required value={labor.operation} onChange={(event) => setLabor({ ...labor, operation: event.target.value })} /></Field>
          <Field label="Исполнитель"><input required value={labor.worker} onChange={(event) => setLabor({ ...labor, worker: event.target.value })} /></Field>
          <Decimal label="Объём выполненной работы, ед." value={labor.quantity} onChange={(quantity) => setLabor({ ...labor, quantity })} />
          <Decimal label="Сдельная расценка, ₽/ед." value={labor.rate} onChange={(rate) => setLabor({ ...labor, rate })} />
          <Field label="Дата работы"><input required type="date" value={labor.date} onChange={(event) => setLabor({ ...labor, date: event.target.value })} /></Field>
          <button className="primary-button" type="submit">Записать сдельную работу</button>
        </fieldset>
      </form>

      <form onSubmit={(event) => void save(event, "/defects", () => {
        if (!activeLayer) throw new Error("Выберите партию сырья.");
        const quantity = number(scrap.quantity, "Количество брака", { positive: true });
        if (quantity > activeLayer.remaining_quantity) throw new Error("Количество брака превышает остаток партии.");
        if (!scrap.reason.trim()) throw new Error("Укажите причину брака.");
        return { layer_id: activeLayer.id, quantity, reason: scrap.reason.trim(), movement_date: scrap.date, event_key: scrap.key };
      }, "Брак списан из выбранной партии сырья.", () => setScrap((item) => ({ ...item, quantity: "", reason: "", key: eventKey() })))}>
        <fieldset disabled={disabled || !permitted("defects") || !data.material_layers.some((item) => item.remaining_quantity > 0)}><legend>Брак сырья</legend><p>Списание уменьшает остаток выбранной партии и сохраняется в журнале.</p>
          <Field label="Партия сырья для списания"><select required value={scrap.layer} onChange={(event) => setScrap({ ...scrap, layer: event.target.value })}><option value="">Выберите партию</option>{data.material_layers.filter((item) => item.remaining_quantity > 0).map((item) => <option key={item.id} value={item.id}>#{item.id} · {item.material_name} · остаток {item.remaining_quantity} {item.unit}</option>)}</select></Field>
          <Decimal label={`Количество брака${activeLayer ? `, ${activeLayer.unit}` : ""}`} value={scrap.quantity} onChange={(quantity) => setScrap({ ...scrap, quantity })} />
          <Field label="Причина брака"><input required value={scrap.reason} onChange={(event) => setScrap({ ...scrap, reason: event.target.value })} /></Field>
          <Field label="Дата списания брака"><input required type="date" value={scrap.date} onChange={(event) => setScrap({ ...scrap, date: event.target.value })} /></Field>
          <button className="primary-button" type="submit">Списать брак</button>
          {!data.material_layers.some((item) => item.remaining_quantity > 0) && <p>Нет партий сырья с положительным остатком.</p>}
        </fieldset>
      </form>

      <form onSubmit={(event) => void save(event, "/releases", () => {
        const product = data.products.find((item) => String(item.nm_id) === release.product);
        const bom = data.boms.find((item) => String(item.id) === release.bom && item.nm_id === product?.nm_id);
        if (!product || !bom) throw new Error("Выберите товар и его версию состава.");
        if (!release.source.trim()) throw new Error("Укажите код задания выпуска.");
        return { source_key: release.source.trim(), nm_id: product.nm_id, quantity: number(release.quantity, "Количество выпуска", { positive: true, integer: true }), production_date: release.date, bom_version_id: bom.id, product_name: product.product_name, supplier_article: product.supplier_article ?? "" };
      }, "Выпуск проведён: материалы и производственные затраты учтены.", () => setRelease((item) => ({ ...item, source: "", quantity: "" })))}>
        <fieldset disabled={disabled || !permitted("releases") || !data.boms.length}><legend>Выпуск по составу изделия</legend><p>Выпуск списывает материалы FIFO и фиксирует работу, упаковку и износ. После проведения сдельные операции в это задание добавить нельзя.</p><p>Повторная отправка тех же данных с тем же кодом задания не создаёт второй выпуск. Для нового или исправленного выпуска после отмены используйте новый код задания.</p>
          <Field label="Код задания выпуска"><input required value={release.source} onChange={(event) => setRelease({ ...release, source: event.target.value })} /></Field>
          <ProductSelect label="Товар для выпуска" products={data.products} value={release.product} onChange={(product) => setRelease({ ...release, product, bom: "" })} />
          <Field label="Версия состава для выпуска"><select required value={release.bom} onChange={(event) => setRelease({ ...release, bom: event.target.value })}><option value="">Выберите версию</option>{data.boms.filter((item) => item.nm_id === Number(release.product)).map((item) => <option key={item.id} value={item.id}>Версия {item.version}{item.note ? ` · ${item.note}` : ""}</option>)}</select></Field>
          <Decimal label="Количество выпуска, целые ед." value={release.quantity} onChange={(quantity) => setRelease({ ...release, quantity })} />
          <Field label="Дата выпуска"><input required type="date" value={release.date} onChange={(event) => setRelease({ ...release, date: event.target.value })} /></Field>
          <button className="primary-button" type="submit">Провести выпуск</button>
          {!data.boms.length && <p>Сначала сохраните состав изделия.</p>}
        </fieldset>
      </form>
      <div className="mfg-journal"><h3>Журнал производственного учёта</h3><p>Сдельных записей: {data.piecework.length}. Списаний брака: {data.defects.length}. Выпусков: {data.releases.length}.</p><p>Отмена восстанавливает учётные остатки. Уже использованную партию отменить нельзя; сервер проверяет это перед действием.</p>
        <h4>Выпуски — последние 10</h4>{!data.releases.length && <p>Выпусков пока нет.</p>}
        {data.releases.slice(0, 10).map((item, index) => <details key={String(item.id ?? index)}><summary>{String(item.production_date ?? "")} · {String(item.source_key ?? "")} · {String(item.quantity ?? "")} ед. · {rub(item.total_cost_rub)}{item.status === "reversed" ? " · Отменён" : ""}</summary><p>На единицу: {rub(item.unit_cost_rub)}. Материалы: {rub(item.material_cost_rub)}. Работа: {rub(item.labor_cost_rub)}. Износ: {rub(item.depreciation_cost_rub)}. Упаковка: {rub(item.packaging_cost_rub)}. Прочие: {rub(item.other_cost_rub)}.</p>{item.movement_id != null && <button type="button" disabled={disabled || !permitted("reverse") || item.status === "reversed"} onClick={(event) => void save(event, `/movements/${Number(item.movement_id)}/reverse`, () => ({}), "Выпуск отменён. Для исправленного выпуска используйте новый код задания.", () => {})}>Отменить выпуск {String(item.source_key ?? item.id)}</button>}</details>)}
        <h4>Сдельная работа — последние 10</h4>{!data.piecework.length && <p>Сдельных записей пока нет.</p>}
        {data.piecework.slice(0, 10).map((item, index) => <p key={String(item.id ?? index)}>{String(item.work_date ?? "")} · {String(item.source_key ?? "")} · {String(item.worker ?? "")} · {String(item.operation_name ?? "")} · {String(item.quantity ?? "")} × {rub(item.rate_per_unit)} = {rub(Number(item.quantity) * Number(item.rate_per_unit))} · {item.release_movement_id ? "Включено в выпуск" : "Ожидает выпуска"}</p>)}
        <h4>Брак — последние 10</h4>{!data.defects.length && <p>Списаний брака пока нет.</p>}
        {data.defects.slice(0, 10).map((item, index) => <div key={String(item.id ?? index)}><p>{String(item.movement_date ?? "")} · {String(item.material_name ?? "")} · {String(item.quantity ?? "")} {String(item.unit ?? "")} · {rub(item.cost_rub)} · {String(item.reason ?? "")}{item.status === "reversed" ? " · Отменено" : ""}</p>{item.movement_id != null && <button type="button" disabled={disabled || !permitted("reverse") || item.status === "reversed"} onClick={(event) => void save(event, `/movements/${Number(item.movement_id)}/reverse`, () => ({}), "Списание брака отменено: сырьё возвращено в учётный остаток.", () => {})}>Отменить списание брака #{String(item.id)}</button>}</div>)}
      </div>
    </div>}
  </section>;
}
