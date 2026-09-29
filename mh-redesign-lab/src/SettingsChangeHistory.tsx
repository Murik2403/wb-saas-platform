import { useEffect, useRef, useState, type FormEvent } from "react";
import { RefreshCw } from "lucide-react";
import { getSettingsChangeHistory, type SettingsChangeHistoryItem, type SettingsChangeKind } from "./data-source";
import "./SettingsChangeHistory.css";

const pageSize = 25;

const labels: Record<SettingsChangeKind, Record<string, string>> = {
  cost: {
    supplier_article: "Артикул продавца",
    cost_per_wb_unit: "Себестоимость единицы WB",
    auto_material_cost: "Авторасход материалов",
    material_cost_rub: "Материалы",
    packaging_cost_rub: "Упаковка",
    packaging_cost_status: "Подтверждение упаковки",
    packaging_cost_source: "Источник стоимости упаковки",
    forecast_refresh_source: "Пересчёт прогноза",
    labor_cost_rub: "Работа",
    other_cost_rub: "Прочие затраты",
  },
  production_norm: {
    supplier_article: "Артикул продавца",
    enabled: "Активность нормы",
    material_per_unit: "Расход материала на комплект",
    target_days: "Целевой срок",
    min_batch: "Минимальная партия",
    blank_type: "Тип заготовки",
    pack_size: "Размер комплекта",
    auto_rules: "Автоправила",
    material_name: "Материал",
  },
};

function displayValue(kind: SettingsChangeKind, field: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Да" : "Нет";
  if (typeof value === "number" && Number.isFinite(value)) {
    const formatted = value.toLocaleString("ru-RU", { maximumSignificantDigits: 15 });
    if (field === "cost_per_wb_unit" || field.endsWith("_rub")) return `${formatted} ₽`;
    if (field === "target_days") return `${formatted} дн.`;
    if (field === "min_batch") return `${formatted} компл.`;
    if (field === "pack_size") return `${formatted} шт.`;
    return formatted;
  }
  if (typeof value === "string") {
    if (field === "packaging_cost_status") return ({ confirmed: "Подтверждена", unknown: "Не подтверждена" } as Record<string, string>)[value] ?? "—";
    if (field === "packaging_cost_source") return ({ manual: "Вручную", unknown: "Не указан" } as Record<string, string>)[value] ?? "Другой источник";
    return value;
  }
  // Snapshot values are deliberately restricted by the API. Avoid rendering arbitrary nested objects.
  return "—";
}

function dateLabel(value: string): string {
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime())
    ? value
    : new Intl.DateTimeFormat("ru-RU", { dateStyle: "medium", timeStyle: "short" }).format(parsed);
}

function sourceLabel(source: string): string {
  if (source === "tenant_api") return "Кабинет";
  if (source === "legacy") return "Настройки";
  if (source === "forecast_refresh") return "Пересчёт прогноза";
  return "Другой способ сохранения";
}

function visibleFields(item: SettingsChangeHistoryItem): Array<{ key: string; label: string; before: string; after: string }> {
  const known = labels[item.kind];
  const keys = Object.keys(item.after).filter((key) => Object.hasOwn(known, key));
  return keys.flatMap((key) => {
    const after = displayValue(item.kind, key, item.after[key]);
    const before = item.before ? displayValue(item.kind, key, item.before[key]) : "";
    if (item.before && Object.is(item.before[key], item.after[key])) return [];
    return [{ key, label: known[key], before, after }];
  });
}

export function SettingsChangeHistory({ refreshKey: externalRefreshKey = 0 }: { refreshKey?: number }) {
  const [kindDraft, setKindDraft] = useState<"" | SettingsChangeKind>("");
  const [nmIdDraft, setNmIdDraft] = useState("");
  const [kind, setKind] = useState<SettingsChangeKind | undefined>();
  const [nmId, setNmId] = useState<number | undefined>();
  const [offset, setOffset] = useState(0);
  const [refreshKey, setRefreshKey] = useState(0);
  const [items, setItems] = useState<SettingsChangeHistoryItem[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const lastExternalRefresh = useRef(externalRefreshKey);

  useEffect(() => {
    let current = true;
    const externalRefresh = lastExternalRefresh.current !== externalRefreshKey;
    lastExternalRefresh.current = externalRefreshKey;
    if (externalRefresh && offset !== 0) {
      setOffset(0);
      return () => { current = false; };
    }
    setLoading(true);
    setError(null);
    void getSettingsChangeHistory({ ...(kind ? { kind } : {}), ...(nmId !== undefined ? { nm_id: nmId } : {}), limit: pageSize, offset })
      .then((page) => {
        if (!current) return;
        setItems(page.items);
        setTotal(page.total);
      })
      .catch((reason: unknown) => {
        if (!current) return;
        setError(reason instanceof Error ? reason.message : "Не удалось загрузить историю изменений");
      })
      .finally(() => { if (current) setLoading(false); });
    return () => { current = false; };
  }, [kind, nmId, offset, refreshKey, externalRefreshKey]);

  const applyFilters = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const value = nmIdDraft.trim();
    const nextNmId = value ? Number(value) : undefined;
    if (value && (!Number.isSafeInteger(nextNmId) || Number(nextNmId) <= 0)) {
      setError("Введите положительный артикул WB.");
      return;
    }
    setKind(kindDraft || undefined);
    setNmId(nextNmId);
    setOffset(0);
  };

  return <section className="work-section settings-change-history" aria-labelledby="settings-change-history-title">
    <div className="settings-history-heading">
      <div><span className="eyebrow">Аудит настроек</span><h2 id="settings-change-history-title">История норм и себестоимости</h2></div>
      <button className="secondary-button" type="button" onClick={() => setRefreshKey((value) => value + 1)} disabled={loading} aria-label="Обновить историю">
        <RefreshCw size={16} /> Обновить историю
      </button>
    </div>
    <p className="settings-history-note">История ведётся с момента включения учёта. Более ранние изменения не восстанавливаются.</p>
    <form className="settings-history-filters" onSubmit={applyFilters}>
      <label><span>Тип изменения</span><select aria-label="Тип изменения" value={kindDraft} onChange={(event) => setKindDraft(event.target.value as "" | SettingsChangeKind)}>
        <option value="">Все изменения</option><option value="cost">Себестоимость</option><option value="production_norm">Нормы производства</option>
      </select></label>
      <label><span>Артикул WB</span><input aria-label="Артикул WB" inputMode="numeric" pattern="[0-9]*" value={nmIdDraft} onChange={(event) => setNmIdDraft(event.target.value)} placeholder="Все товары" /></label>
      <button className="secondary-button" type="submit">Применить фильтры</button>
    </form>

    {error && <div className="settings-history-error" role="alert"><span>{error}</span><button className="ghost-button" type="button" onClick={() => setRefreshKey((value) => value + 1)}>Повторить</button></div>}
    {loading ? <p className="plan-empty" role="status">Загружаем историю…</p> : null}
    {!loading && !error && items.length === 0 ? <p className="plan-empty">Изменений пока нет</p> : null}
    {!loading && !error && items.length > 0 ? <>
      <ol className="settings-history-list">
        {items.map((item) => {
          const fields = visibleFields(item);
          return <li className="settings-history-entry" key={item.id}>
            <div className="settings-history-entry-heading">
              <div><strong>{item.kind === "cost" ? "Изменение себестоимости" : "Изменение нормы производства"}</strong><span>WB {item.nm_id}</span></div>
              <time dateTime={item.changed_at}>{dateLabel(item.changed_at)}</time>
            </div>
            {fields.length ? <dl className="settings-history-values">{fields.map((field) => <div key={field.key}>
              <dt>{field.label}</dt><dd>{item.before === null ? <><span className="settings-history-created">Создано значение</span><strong>{field.after}</strong></> : <><span>{field.before} → {field.after}</span></>}</dd>
            </div>)}</dl> : <p className="plan-empty">Нет отображаемых изменений.</p>}
            {(item.source || item.actor) && <p className="settings-history-meta">{item.source ? <>Источник: {sourceLabel(item.source)}</> : null}{item.source && item.actor ? " · " : null}{item.actor ? <>Кто изменил: {item.actor}</> : null}</p>}
          </li>;
        })}
      </ol>
      <nav className="settings-history-pagination" aria-label="Страницы истории">
        <span>Показано {offset + 1}–{Math.min(offset + items.length, total)} из {total}</span>
        <div><button className="ghost-button" type="button" disabled={offset <= 0 || loading} onClick={() => setOffset((value) => Math.max(0, value - pageSize))}>Предыдущая</button>
          <button className="ghost-button" type="button" disabled={offset + pageSize >= total || loading} onClick={() => setOffset((value) => value + pageSize)}>Следующая</button></div>
      </nav>
    </> : null}
  </section>;
}
