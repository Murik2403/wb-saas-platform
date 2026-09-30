import { scopedFetch as fetch } from "./selected-shop";
import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { API_BASE_URL, apiHeaders } from "./data-source";
import "./ManufacturingCosts.css";

type Material = { material_name: string; unit: string };
type Layer = Material & { id: number; remaining_quantity: number; unit_cost_rub: number };
type Product = { nm_id: number; chrt_id?: number; variant_label?: string; barcodes?: string; product_name: string; supplier_article?: string };
type Equipment = { id: number; name: string; resource_unit: "units" | "hours"; resource_quantity: number; consumed_resource: number; rate_per_resource_unit: number };
type BomComponent = { component_type?: "raw_material" | "semi_finished"; material_name?: string; nm_id?: number; chrt_id?: number; component_bom_version_id?: number; quantity_per_unit: number; unit: string; entered_quantity_per_unit?: string; entered_unit?: string; conversion_factor?: string; normalized_quantity_per_unit?: string; allocations?: Array<{ layer_id: number; units: number; amount_rub: number; source_release_id?: number | null; source_release_key?: string | null; source_bom_version_id?: number | null; source_bom_version_mismatch?: boolean | null; source_provenance?: string }> };
type Bom = { id: number; nm_id: number; chrt_id?: number; version: number; note?: string; materials?: BomComponent[]; operations?: Array<{ name: string; rate_per_unit: number }>; packaging_per_unit?: number; other_per_unit?: number };
type RecordRow = Record<string, unknown>;
export type ManufacturingCostsData = {
  materials: Material[]; material_layers: Layer[]; products: Product[];
  boms: Bom[]; equipment: Equipment[]; piecework: RecordRow[]; defects: RecordRow[]; releases: RecordRow[];
  semi_finished_stock?: Array<{ id: number; nm_id: number; chrt_id: number; ready_units: number; unit_cost_rub: number; source_date?: string | null }>;
  allowed_actions?: string[];
};
type MaterialInput = { component_type: "raw_material" | "semi_finished"; material_name: string; product: string; quantity: string; unit?: string };
type OperationInput = { name: string; rate: string; equipment_id: string; usage: string };
export const materialEntryUnits = (base: string): string[] => [ ["г", "кг"], ["мл", "л"], ["мм", "см", "м"] ].find((family) => family.includes(base)) ?? [base];
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
const productValue = (product: Product, variantAware: boolean) => variantAware && Number(product.chrt_id ?? 0) > 0 ? `${product.nm_id}:${product.chrt_id}` : String(product.nm_id);
function selectedProduct(products: Product[], value: string, variantAware: boolean) {
  const [nm, chrt] = value.split(":").map(Number);
  return products.find((item) => item.nm_id === nm && (!variantAware || Number(item.chrt_id ?? 0) === (chrt || 0)));
}
function applicableBoms(boms: Bom[], product?: Product) {
  if (!product) return [];
  const exact = boms.filter((item) => item.nm_id === product.nm_id && Number(item.chrt_id ?? 0) === Number(product.chrt_id ?? 0));
  if (exact.length || !Number(product.chrt_id ?? 0)) return exact;
  return boms.filter((item) => item.nm_id === product.nm_id && Number(item.chrt_id ?? 0) === 0);
}
function latestApplicableBom(boms: Bom[], product?: Product) {
  return applicableBoms(boms, product).sort((left, right) => right.version - left.version)[0];
}
function routeBlocker(boms: Bom[], products: Product[], target: Product, candidate: Product, depth = 1, seen = new Set<string>()): string | null {
  const targetKey = `${target.nm_id}:${Number(target.chrt_id ?? 0)}`;
  const candidateKey = `${candidate.nm_id}:${Number(candidate.chrt_id ?? 0)}`;
  if (candidateKey === targetKey) return "Нельзя включить товар в его собственный состав: это создаст цикл.";
  if (seen.has(candidateKey)) return "В цепочке составов уже найден цикл. Проверьте спецификации полуфабрикатов.";
  if (depth > 8) return "Цепочка полуфабрикатов длиннее 8 уровней и не может быть сохранена.";
  const childBom = latestApplicableBom(boms, candidate);
  if (!childBom) return "Для этого SKU и варианта сначала сохраните состав изделия.";
  const nextSeen = new Set(seen); nextSeen.add(candidateKey);
  for (const component of childBom.materials ?? []) {
    if (component.component_type !== "semi_finished" || component.nm_id == null) continue;
    const child = products.find((item) => item.nm_id === component.nm_id && Number(item.chrt_id ?? 0) === Number(component.chrt_id ?? 0));
    if (!child) return `В составе полуфабриката WB ${component.nm_id} отсутствует SKU/вариант в каталоге.`;
    const blocker = routeBlocker(boms, products, target, child, depth + 1, nextSeen);
    if (blocker) return blocker;
  }
  return null;
}
function ProductSelect({ products, value, onChange, label, variantAware = false }: { products: Product[]; value: string; onChange: (value: string) => void; label: string; variantAware?: boolean }) {
  const options = variantAware ? products : [...new Map(products.map((item) => [item.nm_id, item])).values()];
  return <Field label={label}><select required value={value} onChange={(event) => onChange(event.target.value)}><option value="">Выберите товар</option>{options.map((item) => <option key={`${item.nm_id}:${item.chrt_id ?? 0}`} value={productValue(item, variantAware)}>{item.product_name || `WB ${item.nm_id}`} · {item.nm_id}{variantAware && Number(item.chrt_id ?? 0) > 0 ? ` · ${item.variant_label || `вариант ${item.chrt_id}`}` : ""}</option>)}</select></Field>;
}
function SemiFinishedProvenance({ materials }: { materials: unknown }) {
  if (!Array.isArray(materials)) return null;
  const components = (materials as BomComponent[]).filter((item) => item.component_type === "semi_finished");
  if (!components.length) return null;
  return <ul className="mfg-component-provenance">{components.map((component, index) => <li key={`${component.nm_id}:${component.chrt_id ?? 0}:${index}`}>
    Полуфабрикат WB {component.nm_id}{component.chrt_id ? ` · вариант ${component.chrt_id}` : ""}; закреплённая спецификация v{component.component_bom_version_id ?? "не указана"}.
    <ul>{(component.allocations ?? []).map((allocation) => <li key={allocation.layer_id}>
      {allocation.units} шт из партии #{allocation.layer_id}{allocation.source_release_key ? ` (${allocation.source_release_key})` : ""}: {allocation.source_bom_version_id == null
        ? `версия источника неизвестна (${allocation.source_provenance === "unknown_legacy_or_external" ? "начальный или внешний остаток" : "старый выпуск без связи с рецептом"}); использована стоимость партии ${rub(allocation.amount_rub)}.`
        : allocation.source_bom_version_mismatch
          ? `версия источника BOM v${allocation.source_bom_version_id} отличается от закреплённой v${component.component_bom_version_id}; FIFO учёл фактическую стоимость партии ${rub(allocation.amount_rub)}.`
          : `версия источника BOM v${allocation.source_bom_version_id} совпадает с закреплённой; FIFO учёл стоимость партии ${rub(allocation.amount_rub)}.`}
    </li>)}</ul>
  </li>)}</ul>;
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
  const [components, setComponents] = useState<MaterialInput[]>([{ component_type: "raw_material", material_name: "", product: "", quantity: "1" }]);
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
  const bomRouteBlocked = Boolean(data && components.some((item) => {
    if (item.component_type !== "semi_finished") return false;
    const target = selectedProduct(data.products, bomProduct, true);
    const child = selectedProduct(data.products, item.product, true);
    return !target || !child || Boolean(routeBlocker(data.boms, data.products, target, child));
  }));
  const releaseProduct = data && selectedProduct(data.products, release.product, true);
  const releaseBom = data && applicableBoms(data.boms, releaseProduct || undefined).find((item) => String(item.id) === release.bom);
  const releaseQty = Number(release.quantity.replace(",", "."));
  const semiPreview = (releaseBom?.materials ?? []).filter((item) => item.component_type === "semi_finished").map((item) => {
    const needed = releaseQty * item.quantity_per_unit;
    const layers = (data?.semi_finished_stock ?? []).filter((layer) => layer.nm_id === item.nm_id && layer.chrt_id === Number(item.chrt_id ?? 0) && (!layer.source_date || layer.source_date <= release.date)).sort((a, b) => (a.source_date ?? "1900-01-01").localeCompare(b.source_date ?? "1900-01-01") || a.id - b.id);
    const available = layers.reduce((sum, layer) => sum + layer.ready_units, 0);
    let remaining = needed, cost = 0, count = 0;
    for (const layer of layers) { const take = Math.min(layer.ready_units, Math.max(remaining, 0)); if (take > 0) { count++; cost += Math.round(take * layer.unit_cost_rub * 100) / 100; remaining -= take; } }
    return { item, needed, available, cost, count, blocked: releaseQty > 0 && (!Number.isInteger(needed) || (data?.semi_finished_stock !== undefined && available < needed)) };
  });
  const releaseBlocked = semiPreview.some((entry) => entry.blocked);
  return <section className="manufacturing-costs" aria-labelledby="mfg-title" aria-busy={busy || loading}>
    <div className="mfg-heading"><div><h2 id="mfg-title">Состав изделия и затраты производства</h2><p>Материалы, работа и износ оборудования входят в стоимость выпуска. Списанный брак учитывается отдельно.</p><p>Это стоимость выпуска и запасов. Финансовый отчёт использует историческую стоимость FIFO при точном сопоставлении продаж; неполные данные помечаются как предварительные. Выпуск сам по себе не является расходом от продаж.</p></div><button type="button" className="ghost-button" disabled={busy || loading} onClick={() => setReloadKey((value) => value + 1)}>Обновить данные</button></div>
    {(!canEdit || data?.allowed_actions?.length === 0) && <p>У вашей роли нет прав на изменение производственного учёта. Данные доступны для просмотра.</p>}
    {canEdit && data?.allowed_actions && data.allowed_actions.length > 0 && data.allowed_actions.length < 6 && <p>Доступные действия ограничены вашей ролью. Остальные формы доступны для просмотра.</p>}
    {loading && <p role="status">Загружаем производственный учёт…</p>}
    {error && <p role="alert" className="mfg-error">{error}</p>}
    {success && <p role="status" className="mfg-success">{success}</p>}
    {data && <div className="mfg-panels">
      <form onSubmit={(event) => void save(event, "/boms", () => {
        const product = selectedProduct(data.products, bomProduct, true);
        if (!product) throw new Error("Выберите товар и вариант.");
        const identities = components.map((item) => item.component_type === "raw_material" ? `raw:${item.material_name}` : `semi:${item.product}`);
        if (new Set(identities).size !== identities.length) throw new Error("Каждый материал или полуфабрикат добавьте в состав один раз.");
        const target = product;
        return {
          nm_id: product.nm_id,
          chrt_id: Number(product.chrt_id ?? 0),
          materials: components.map((item) => {
            const quantity = number(item.quantity, "Расход компонента", { positive: true });
            if (item.component_type === "semi_finished") {
              const child = selectedProduct(data.products, item.product, true);
              if (!child) throw new Error("Выберите SKU и вариант полуфабриката из каталога.");
              const blocker = routeBlocker(data.boms, data.products, target, child);
              if (blocker) throw new Error(blocker);
              const childBom = latestApplicableBom(data.boms, child)!;
              return { component_type: "semi_finished" as const, nm_id: child.nm_id, chrt_id: Number(child.chrt_id ?? 0), component_bom_version_id: childBom.id, unit: "шт", quantity_per_unit: quantity };
            }
            const material = data.materials.find((row) => row.material_name === item.material_name);
            if (!material) throw new Error("Выберите материал из справочника.");
            return { material_name: material.material_name, unit: item.unit || material.unit, quantity_per_unit: quantity };
          }),
          operations: operations.map((item) => { if (!item.name.trim()) throw new Error("Укажите название операции."); return { name: item.name.trim(), rate_per_unit: number(item.rate, "Расценка", { money: true }), ...(item.equipment_id ? { equipment_id: number(item.equipment_id, "Оборудование", { integer: true, positive: true }), equipment_usage_per_unit: number(item.usage, "Расход ресурса", { positive: true }) } : {}) }; }),
          packaging_per_unit: number(packaging, "Упаковка", { money: true }), other_per_unit: number(other, "Прочие затраты", { money: true }), note: bomNote.trim(),
        };
      }, "Новая версия состава изделия сохранена.", () => setBomNote(""))}>
        <fieldset disabled={disabled || !permitted("boms") || !data.products.length}><legend>Состав изделия — новая версия</legend>
          <p>Расход указывается на одну продаваемую единицу. Единицу нормы можно выбрать в той же размерности: г/кг, мл/л, мм/см/м. Сервер переводит норму в единицу справочника без округления (от 0,001, до 6 знаков). Перевод массы в объём, рулонов или упаковок не выполняется. Сохранение создаёт новую версию; прежние выпуски сохраняют свою себестоимость.</p>
          {!data.products.length ? <p>Сначала добавьте товары в справочник.</p> : null}
          <ProductSelect label="Товар и вариант для состава изделия" products={data.products} value={bomProduct} onChange={setBomProduct} variantAware />
          {components.map((item, index) => { const material = data.materials.find((row) => row.material_name === item.material_name); const target = selectedProduct(data.products, bomProduct, true); const child = selectedProduct(data.products, item.product, true); const blocker = target && child ? routeBlocker(data.boms, data.products, target, child) : null; return <div className="mfg-row" key={index}>
            <Field label={`Тип компонента ${index + 1}`}><select value={item.component_type} onChange={(event) => updateComponent(index, { component_type: event.target.value as MaterialInput["component_type"], material_name: "", product: "", unit: "" })}><option value="raw_material">Сырьё / материал</option><option value="semi_finished">Полуфабрикат</option></select></Field>
            {item.component_type === "semi_finished" ? <>
              <Field label={`Полуфабрикат ${index + 1}`}><select required value={item.product} onChange={(event) => updateComponent(index, { product: event.target.value })}><option value="">Выберите SKU и вариант</option>{data.products.map((candidate) => { const reason = target ? routeBlocker(data.boms, data.products, target, candidate) : !applicableBoms(data.boms, candidate).length ? "Сначала сохраните состав" : null; return <option key={`${candidate.nm_id}:${candidate.chrt_id ?? 0}`} value={productValue(candidate, true)} disabled={Boolean(reason)}>{candidate.product_name || `WB ${candidate.nm_id}`} · WB {candidate.nm_id}{Number(candidate.chrt_id ?? 0) > 0 ? ` · ${candidate.variant_label || `вариант ${candidate.chrt_id}`}` : ""}{reason ? ` — ${reason}` : ""}</option>; })}</select></Field>
              <Decimal label={`Расход полуфабриката ${index + 1}, шт/изделие`} value={item.quantity} onChange={(quantity) => updateComponent(index, { quantity })} />
              {blocker && <p className="mfg-error" role="status">{blocker}</p>}
              {!target && <p role="status">Сначала выберите товар, для которого сохраняется состав.</p>}
            </> : <>
              <Field label={`Материал ${index + 1}`}><select required value={item.material_name} onChange={(event) => updateComponent(index, { material_name: event.target.value, unit: "" })}><option value="">Выберите материал</option>{data.materials.map((row) => <option key={row.material_name} value={row.material_name}>{row.material_name} ({row.unit})</option>)}</select></Field>
              {!data.materials.length && <p role="status">Сырьё недоступно: добавьте материал в справочник или переключите тип компонента на полуфабрикат.</p>}
              {material && <Field label={`Единица нормы материала ${index + 1}`}><select value={item.unit || material.unit} onChange={(event) => updateComponent(index, { unit: event.target.value })}>{materialEntryUnits(material.unit).map((unit) => <option key={unit} value={unit}>{unit}</option>)}</select></Field>}
              <Decimal label={`Расход материала ${index + 1}${material ? `, ${item.unit || material.unit}` : ""}`} value={item.quantity} onChange={(quantity) => updateComponent(index, { quantity })} />
            </>}
            <button type="button" disabled={components.length === 1} onClick={() => setComponents((items) => items.filter((_, i) => i !== index))} aria-label={`Удалить компонент ${index + 1}`}>Удалить</button>
          </div>; })}
          <button type="button" onClick={() => setComponents((items) => [...items, { component_type: "raw_material", material_name: "", product: "", quantity: "1" }])}>Добавить компонент</button>
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
          <button className="primary-button" type="submit" disabled={disabled || !permitted("boms") || bomRouteBlocked}>Сохранить новую версию</button>
          {data.boms.filter((item) => { const product = selectedProduct(data.products, bomProduct, true); return !product || (item.nm_id === product.nm_id && Number(item.chrt_id ?? 0) === Number(product.chrt_id ?? 0)); }).map((item) => <details key={item.id}><summary>WB {item.nm_id}{Number(item.chrt_id ?? 0) > 0 ? ` · вариант ${item.chrt_id}` : ""} · версия {item.version}{item.note ? ` · ${item.note}` : ""}</summary><ul>{item.materials?.map((component, index) => <li key={`${component.component_type ?? "raw"}:${component.material_name ?? component.nm_id}:${index}`}>{component.component_type === "semi_finished" ? `Полуфабрикат WB ${component.nm_id}${component.chrt_id ? ` · вариант ${component.chrt_id}` : ""} · состав v${component.component_bom_version_id}` : `${component.material_name}: ${component.entered_quantity_per_unit && component.entered_unit ? `${component.entered_quantity_per_unit} ${component.entered_unit} = ` : ""}${component.normalized_quantity_per_unit ?? component.quantity_per_unit} ${component.unit}/изделие`}</li>)}{item.operations?.map((operation, index) => <li key={index}>{operation.name}: {rub(operation.rate_per_unit)}/изделие</li>)}</ul><p>Упаковка: {rub(item.packaging_per_unit)}/изделие. Прочие: {rub(item.other_per_unit)}/изделие.</p></details>)}
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
        const product = selectedProduct(data.products, labor.product, true);
        if (!product) throw new Error("Выберите товар и вариант.");
        return { source_key: labor.source.trim(), nm_id: product.nm_id, chrt_id: Number(product.chrt_id ?? 0), operation_name: labor.operation.trim(), worker: labor.worker.trim(), quantity: number(labor.quantity, "Объём работы", { positive: true }), rate_per_unit: number(labor.rate, "Расценка работы", { money: true }), work_date: labor.date, event_key: labor.key };
      }, "Сдельная работа сохранена и будет включена в выпуск этого задания.", () => setLabor((item) => ({ ...item, quantity: "", key: eventKey() })))}>
        <fieldset disabled={disabled || !permitted("piecework") || !data.products.length}><legend>Сдельная работа по заданию</legend><p>Внесите все выполненные операции до выпуска. Код задания должен совпадать с кодом в форме выпуска ниже.</p>
          <Field label="Код задания для работы"><input required value={labor.source} onChange={(event) => setLabor({ ...labor, source: event.target.value })} /></Field>
          <ProductSelect label="Товар и вариант для сдельной работы" products={data.products} value={labor.product} onChange={(product) => setLabor({ ...labor, product })} variantAware />
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
        const product = selectedProduct(data.products, release.product, true);
        const bom = applicableBoms(data.boms, product).find((item) => String(item.id) === release.bom);
        if (!product || !bom) throw new Error("Выберите товар и его версию состава.");
        if (releaseBlocked) throw new Error("Недостаточно полуфабрикатов или требуется дробное число единиц.");
        if (!release.source.trim()) throw new Error("Укажите код задания выпуска.");
        return { source_key: release.source.trim(), nm_id: product.nm_id, chrt_id: Number(product.chrt_id ?? 0), quantity: number(release.quantity, "Количество выпуска", { positive: true, integer: true }), production_date: release.date, bom_version_id: bom.id, product_name: product.product_name, supplier_article: product.supplier_article ?? "" };
      }, "Выпуск проведён: материалы и производственные затраты учтены.", () => setRelease((item) => ({ ...item, source: "", quantity: "" })))}>
        <fieldset disabled={disabled || !permitted("releases") || !data.boms.length}><legend>Выпуск по составу изделия</legend><p>Выпуск списывает материалы FIFO и фиксирует работу, упаковку и износ. После проведения сдельные операции в это задание добавить нельзя.</p><p>Повторная отправка тех же данных с тем же кодом задания не создаёт второй выпуск. Для нового или исправленного выпуска после отмены используйте новый код задания.</p>
          <Field label="Код задания выпуска"><input required value={release.source} onChange={(event) => setRelease({ ...release, source: event.target.value })} /></Field>
          <ProductSelect label="Товар и вариант для выпуска" products={data.products} value={release.product} onChange={(product) => setRelease({ ...release, product, bom: "" })} variantAware />
          <Field label="Версия состава для выпуска"><select required value={release.bom} onChange={(event) => setRelease({ ...release, bom: event.target.value })}><option value="">Выберите версию</option>{applicableBoms(data.boms, selectedProduct(data.products, release.product, true)).map((item) => <option key={item.id} value={item.id}>{Number(item.chrt_id ?? 0) === 0 && selectedProduct(data.products, release.product, true)?.chrt_id ? "Общий состав · " : ""}Версия {item.version}{item.note ? ` · ${item.note}` : ""}</option>)}</select></Field>
          <Decimal label="Количество выпуска, целые ед." value={release.quantity} onChange={(quantity) => setRelease({ ...release, quantity })} />
          <Field label="Дата выпуска"><input required type="date" value={release.date} onChange={(event) => setRelease({ ...release, date: event.target.value })} /></Field>
          {semiPreview.length > 0 && <div aria-label="Проверка полуфабрикатов"><p>Предварительная проверка на дату выпуска. Сервер повторно проверит остатки при проведении.</p><ul>{semiPreview.map(({ item, needed, available, cost, count, blocked }) => <li key={`${item.nm_id}:${item.chrt_id}`}>WB {item.nm_id} · вариант {item.chrt_id ?? 0} · спецификация #{item.component_bom_version_id}: нужно {needed || 0} шт, доступно {available} шт, партий FIFO {count}, стоимость {rub(cost)}.{blocked && <strong> Выпуск заблокирован: проверьте остаток и целое количество.</strong>}</li>)}</ul></div>}
          <button className="primary-button" type="submit" disabled={releaseBlocked}>Провести выпуск</button>
          {!data.boms.length && <p>Сначала сохраните состав изделия.</p>}
        </fieldset>
      </form>
      <div className="mfg-journal"><h3>Журнал производственного учёта</h3><p>Сдельных записей: {data.piecework.length}. Списаний брака: {data.defects.length}. Выпусков: {data.releases.length}.</p><p>Отмена восстанавливает учётные остатки. Уже использованную партию отменить нельзя; сервер проверяет это перед действием.</p>
        <h4>Выпуски — последние 10</h4>{!data.releases.length && <p>Выпусков пока нет.</p>}
        {data.releases.slice(0, 10).map((item, index) => <details key={String(item.id ?? index)}><summary>{String(item.production_date ?? "")} · {String(item.source_key ?? "")} · WB {String(item.nm_id ?? "")}{Number(item.chrt_id ?? 0) > 0 ? ` · вариант ${item.chrt_id}` : ""} · {String(item.quantity ?? "")} ед. · {rub(item.total_cost_rub)}{item.status === "reversed" ? " · Отменён" : ""}</summary><p>На единицу: {rub(item.unit_cost_rub)}. Материалы: {rub(item.material_cost_rub)}. Работа: {rub(item.labor_cost_rub)}. Износ: {rub(item.depreciation_cost_rub)}. Упаковка: {rub(item.packaging_cost_rub)}. Прочие: {rub(item.other_cost_rub)}.</p><SemiFinishedProvenance materials={item.materials} />{item.movement_id != null && <button type="button" disabled={disabled || !permitted("reverse") || item.status === "reversed"} onClick={(event) => void save(event, `/movements/${Number(item.movement_id)}/reverse`, () => ({}), "Выпуск отменён. Для исправленного выпуска используйте новый код задания.", () => {})}>Отменить выпуск {String(item.source_key ?? item.id)}</button>}</details>)}
        <h4>Сдельная работа — последние 10</h4>{!data.piecework.length && <p>Сдельных записей пока нет.</p>}
        {data.piecework.slice(0, 10).map((item, index) => <p key={String(item.id ?? index)}>{String(item.work_date ?? "")} · {String(item.source_key ?? "")} · {String(item.worker ?? "")} · {String(item.operation_name ?? "")} · {String(item.quantity ?? "")} × {rub(item.rate_per_unit)} = {rub(Number(item.quantity) * Number(item.rate_per_unit))} · {item.release_movement_id ? "Включено в выпуск" : "Ожидает выпуска"}</p>)}
        <h4>Брак — последние 10</h4>{!data.defects.length && <p>Списаний брака пока нет.</p>}
        {data.defects.slice(0, 10).map((item, index) => <div key={String(item.id ?? index)}><p>{String(item.movement_date ?? "")} · {String(item.material_name ?? "")} · {String(item.quantity ?? "")} {String(item.unit ?? "")} · {rub(item.cost_rub)} · {String(item.reason ?? "")}{item.status === "reversed" ? " · Отменено" : ""}</p>{item.movement_id != null && <button type="button" disabled={disabled || !permitted("reverse") || item.status === "reversed"} onClick={(event) => void save(event, `/movements/${Number(item.movement_id)}/reverse`, () => ({}), "Списание брака отменено: сырьё возвращено в учётный остаток.", () => {})}>Отменить списание брака #{String(item.id)}</button>}</div>)}
      </div>
    </div>}
  </section>;
}
