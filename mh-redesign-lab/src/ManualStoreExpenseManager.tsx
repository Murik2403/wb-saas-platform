import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  confirmStoreExpenseImport,
  createManualStoreExpense,
  createRecurringStoreExpenseTemplate,
  deleteManualStoreExpense,
  generateRecurringStoreExpenses,
  getManualStoreExpenses,
  getRecurringStoreExpenseTemplates,
  previewStoreExpenseImport,
  setRecurringStoreExpenseTemplateActive,
  updateManualStoreExpenseInclusion,
} from "./data-source";
import type { RecurringStoreExpenseTemplate, StoreExpense, StoreExpenseImportPreview } from "./data-source";

const articles = [
  ["purchase_production", "Закупка / производство"],
  ["logistics_returns", "Логистика и возвраты"],
  ["storage", "Хранение"],
  ["acceptance", "Приёмка"],
  ["advertising", "Реклама"],
  ["packaging_labor", "Упаковка и труд"],
  ["defects_losses", "Брак и потери"],
  ["taxes", "Налоги"],
] as const;

function rub(value: number) {
  return `${Number(value).toLocaleString("ru-RU", { maximumFractionDigits: 2 })} ₽`;
}

export function ManualStoreExpenseManager() {
  const [items, setItems] = useState<StoreExpense[]>([]);
  const [templates, setTemplates] = useState<RecurringStoreExpenseTemplate[]>([]);
  const [preview, setPreview] = useState<StoreExpenseImportPreview | null>(null);
  const [month, setMonth] = useState(() => {
    const now = new Date();
    return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}`;
  });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const response = await getManualStoreExpenses();
    setItems(response.items);
  }, []);

  const refreshTemplates = useCallback(async () => {
    const response = await getRecurringStoreExpenseTemplates();
    setTemplates(response.items);
  }, []);

  useEffect(() => {
    void refresh().catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Не удалось загрузить общие расходы"));
  }, [refresh]);

  useEffect(() => {
    void refreshTemplates().catch((reason: unknown) => setError(reason instanceof Error ? reason.message : "Не удалось загрузить шаблоны расходов"));
  }, [refreshTemplates]);

  const save = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await createManualStoreExpense({
        article: String(values.get("article") || "taxes"),
        amount_rub: Number(values.get("amount_rub")),
        source: "manual",
        period_start: String(values.get("period_start") || ""),
        period_end: String(values.get("period_end") || ""),
        note: String(values.get("note") || ""),
        include_in_management_result: values.get("include_in_management_result") === "on",
      });
      form.reset();
      setMessage("Общий расход сохранён");
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось сохранить общий расход");
    } finally {
      setBusy(false);
    }
  };

  const setInclusion = async (item: StoreExpense) => {
    setBusy(true);
    setError(null);
    try {
      await updateManualStoreExpenseInclusion(item.id, !item.include_in_management_result);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось изменить учёт общего расхода");
    } finally {
      setBusy(false);
    }
  };

  const remove = async (item: StoreExpense) => {
    setBusy(true);
    setError(null);
    try {
      await deleteManualStoreExpense(item.id);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось удалить общий расход");
    } finally {
      setBusy(false);
    }
  };

  const saveTemplate = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = event.currentTarget;
    const values = new FormData(form);
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await createRecurringStoreExpenseTemplate({
        article: String(values.get("template_article") || "taxes"),
        amount_rub: Number(values.get("template_amount_rub")),
        note: String(values.get("template_note") || ""),
        active: values.get("template_active") === "on",
      });
      form.reset();
      setMessage("Регулярный шаблон сохранён. Расходы появятся только после ручного запуска месяца.");
      await refreshTemplates();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось сохранить шаблон расхода");
    } finally {
      setBusy(false);
    }
  };

  const generateMonth = async () => {
    const [year, monthNumber] = month.split("-").map(Number);
    if (!year || !monthNumber) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await generateRecurringStoreExpenses(year, monthNumber);
      setMessage(`Создано расходов: ${result.created}; уже создано ранее: ${result.already_exists}. Все новые расходы исключены из результата до отдельного подтверждения.`);
      await refresh();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось создать расходы за месяц");
    } finally {
      setBusy(false);
    }
  };

  const toggleTemplateActive = async (template: RecurringStoreExpenseTemplate) => {
    const active = !template.active;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      await setRecurringStoreExpenseTemplateActive(template.id, active);
      await refreshTemplates();
      setMessage(active ? "Шаблон включён. Он будет использован только после ручного запуска месяца." : "Шаблон отключён. Уже созданные расходы не изменены.");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось изменить активность шаблона");
    } finally {
      setBusy(false);
    }
  };

  const previewImport = async (file?: File) => {
    if (!file) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    setPreview(null);
    try {
      setPreview(await previewStoreExpenseImport(file));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось проверить CSV-файл");
    } finally {
      setBusy(false);
    }
  };

  const confirmImport = async () => {
    if (!preview || preview.valid_count <= 0 || preview.already_imported) return;
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await confirmStoreExpenseImport(preview.batch_id);
      setMessage(result.already_confirmed
        ? `Этот импорт уже был подтверждён. Добавлено расходов: ${result.created_count}.`
        : `Добавлен${result.created_count === 1 ? "" : "о"} расход${result.created_count === 1 ? "" : "ов"}: ${result.created_count}. Все импортированные расходы исключены до отдельного подтверждения.`);
      await refresh();
      setPreview(null);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Не удалось подтвердить импорт");
    } finally {
      setBusy(false);
    }
  };

  return <div className="manual-store-expenses">
    <h3>Общие расходы магазина</h3>
    <p className="form-hint">Для аренды, налогов и других сумм без артикула. Расход учитывается по дням выбранного периода и по умолчанию исключён из результата.</p>
    <form className="settings-form" aria-label="Добавить общий расход магазина" onSubmit={(event) => { void save(event); }}>
      <label className="operation-field"><span>Статья общего расхода</span><select name="article" defaultValue="taxes">{articles.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
      <label className="operation-field"><span>Сумма за период, ₽</span><input name="amount_rub" min="0.01" required step="0.01" type="number" /></label>
      <label className="operation-field"><span>Период от</span><input name="period_start" required type="date" /></label>
      <label className="operation-field"><span>Период до</span><input name="period_end" required type="date" /></label>
      <label className="operation-field"><span>Примечание / источник</span><input name="note" placeholder="Например, аренда цеха за сентябрь" /></label>
      <label className="checkbox-field"><input name="include_in_management_result" type="checkbox" /><span>Проверил, что это внешний расход и он не задвоен с данными WB, рекламой и себестоимостью; учесть его</span></label>
      <button className="primary-button" disabled={busy} type="submit">{busy ? "Сохраняем" : "Добавить общий расход"}</button>
    </form>
    <section aria-labelledby="recurring-expenses-title">
      <h4 id="recurring-expenses-title">Регулярные расходы</h4>
      <p className="form-hint">Шаблон сам ничего не добавляет. При ручном запуске расход создаётся за полный календарный месяц — с первого по последний день — чтобы сумма корректно распределялась по дням выбранного периода. Созданные суммы останутся исключёнными из результата до отдельного подтверждения.</p>
      <form className="settings-form" aria-label="Создать шаблон регулярного расхода" onSubmit={(event) => { void saveTemplate(event); }}>
        <label className="operation-field"><span>Статья шаблона</span><select name="template_article" defaultValue="taxes">{articles.map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>
        <label className="operation-field"><span>Сумма регулярного расхода, ₽</span><input name="template_amount_rub" min="0.01" required step="0.01" type="number" /></label>
        <label className="operation-field"><span>Примечание шаблона</span><input name="template_note" /></label>
        <label className="checkbox-field"><input name="template_active" type="checkbox" defaultChecked /><span>Шаблон активен</span></label>
        <button className="primary-button" disabled={busy} type="submit">Сохранить шаблон</button>
      </form>
      {templates.length ? <div className="cost-settings-list">{templates.map((template) => <div className="cost-setting-row" key={`template:${template.id}`}>
        <div className="cost-setting-product"><strong>{articles.find(([value]) => value === template.article)?.[1] ?? template.article}</strong><span>Полный календарный месяц · {template.note || "без примечания"} · {template.active ? "активен" : "неактивен"}</span></div>
        <div className="cost-setting-amount"><span>Сумма</span><strong>{rub(template.amount_rub)}</strong></div>
        <button className="ghost-button" disabled={busy} type="button" onClick={() => { void toggleTemplateActive(template); }}>{template.active ? "Отключить" : "Включить"}</button>
      </div>)}</div> : <p className="plan-empty">Шаблонов пока нет.</p>}
      <div className="settings-form">
        <label className="operation-field"><span>Месяц для создания расходов</span><input aria-label="Месяц для создания расходов" type="month" value={month} onChange={(event) => setMonth(event.target.value)} /></label>
        <button className="primary-button" disabled={busy || templates.length === 0 || templates.every((template) => !template.active)} type="button" onClick={() => { void generateMonth(); }}>Создать расходы за месяц</button>
      </div>
    </section>
    <section aria-labelledby="expense-import-title">
      <h4 id="expense-import-title">Импорт CSV</h4>
      <p className="form-hint">Сначала проверьте строки и предупреждения. Предпросмотр ничего не сохраняет в реестр; импортированные расходы по умолчанию исключены из результата.</p>
      <label className="operation-field"><span>CSV-файл расходов</span><input accept=".csv,text/csv" type="file" onChange={(event) => { void previewImport(event.currentTarget.files?.[0]); }} /></label>
      {preview ? <div aria-label="Предпросмотр импорта" className="cost-settings-list">
        {preview.already_imported ? <p className="form-hint" role="status">Этот файл уже импортирован. Повторное подтверждение не создаст дубликаты.</p> : null}
        <p className="form-hint">Готово к импорту: {preview.valid_count}; ошибок: {preview.invalid_count}; возможных дублей: {preview.duplicate_count}.</p>
        {preview.rows.map((row) => <div className="cost-setting-row" key={`csv:${row.line_number}`}>
          <div className="cost-setting-product"><strong>Строка {row.line_number}: {articles.find(([value]) => value === row.article)?.[1] ?? (row.article || "Без статьи")}</strong><span>{row.period_start || "период не указан"} — {row.period_end || "период не указан"} · {row.note || "без примечания"}</span>
            {row.errors.map((rowError, index) => <span className="operation-error" key={`${row.line_number}:${index}`}>{rowError}</span>)}
            {row.duplicate ? <span className="muted-tag">Уже есть в реестре</span> : null}
          </div>
          <div className="cost-setting-amount"><span>Сумма</span><strong>{rub(row.amount_rub)}</strong></div>
        </div>)}
        <button className="primary-button" disabled={busy || preview.valid_count <= 0 || preview.already_imported} type="button" onClick={() => { void confirmImport(); }}>Подтвердить импорт</button>
        {preview.valid_count <= 0 ? <p className="plan-empty">Нет строк, доступных для подтверждения.</p> : null}
      </div> : null}
    </section>
    {error ? <p className="operation-error" role="alert">{error}</p> : null}
    {message ? <p className="form-hint" role="status">{message}</p> : null}
    {items.length ? <div className="cost-settings-list">{items.map((item) => <div className="cost-setting-row" key={`store:${item.id}`}>
      <div className="cost-setting-product"><strong>{articles.find(([value]) => value === item.article)?.[1] ?? item.article}</strong><span>Весь магазин · {item.period_start} — {item.period_end} · {item.note || "без примечания"} · {item.include_in_management_result ? "включён после подтверждения" : "исключён до подтверждения"}</span></div>
      <div className="cost-setting-amount"><span>Сумма за период</span><strong>{rub(item.amount_rub)}</strong></div>
      {item.article === "wb_commission" ? <span className="muted-tag">Комиссия WB не вычитается повторно</span> : <button className="ghost-button" disabled={busy} type="button" onClick={() => void setInclusion(item)}>{item.include_in_management_result ? "Исключить" : "Подтвердить и включить"}</button>}
      <button className="ghost-button" disabled={busy} type="button" onClick={() => void remove(item)}>Удалить</button>
    </div>)}</div> : <p className="plan-empty">Общих расходов пока нет.</p>}
  </div>;
}
