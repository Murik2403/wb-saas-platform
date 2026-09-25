import { useEffect, useState } from "react";
import { getManagementPnl } from "./data-source";
import type { DashboardPeriod, ManagementPnl, ManagementPnlExpense } from "./data-source";

function money(value: number | null): string {
  return value === null ? "не рассчитано" : `${Math.round(value).toLocaleString("ru-RU")} ₽`;
}

function ExpenseLines({ title, items }: { title: string; items: ManagementPnlExpense[] }) {
  if (!items.length) return null;
  return <div className="management-pnl-lines"><strong>{title}</strong>{items.slice(0, 5).map((item) => <span key={`${item.scope ?? "sku"}:${item.id}`}>{item.scope === "store" ? "Весь магазин" : `WB ${item.nm_id ?? "—"}`} · {item.article} · {money(item.allocated_rub)}{item.exclusion_reason ? ` · ${item.exclusion_reason}` : ""}</span>)}{items.length > 5 ? <small>И ещё {items.length - 5} строк</small> : null}</div>;
}

export function ManagementPnlSummary({ period }: { period: DashboardPeriod }) {
  const [data, setData] = useState<ManagementPnl | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setLoading(true);
    setError(null);
    getManagementPnl(period, controller.signal)
      .then((result) => setData(result))
      .catch((reason: unknown) => {
        if (!controller.signal.aborted) setError(reason instanceof Error ? reason.message : "Не удалось загрузить управленческий результат");
      })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [period.id, period.start, period.end]);

  return <section className="panel management-pnl-summary" aria-label="Управленческий результат всего магазина">
    <div className="section-header">
      <div><div className="eyebrow">P&amp;L · весь магазин</div><h2>Управленческий результат</h2></div>
      {data?.data_status === "ok" ? <span className="status-pill good">расчёт готов</span> : null}
    </div>
    {loading ? <p className="form-hint">Считаем результат по выбранному периоду…</p> : null}
    {error ? <p className="operation-error" role="alert">{error}</p> : null}
    {data && !loading ? <>
      {data.data_status === "no_financial_data" ? <p className="form-hint">Нет финансовых данных WB за выбранный период; ручные расходы не применены.</p> : data.data_status === "missing_store_profit" ? <p className="operation-error" role="alert">Не удалось рассчитать прибыль магазина по финансовому снимку. Управленческий результат не показан; проверьте данные периода и обновите отчёт.</p> : <>
        <div className="cash-grid management-pnl-metrics">
          <div className="stat"><span>Управленческая прибыль</span><strong>{money(data.management_profit_rub)}</strong><small>после подтверждённых внешних расходов</small></div>
          <div className="stat"><span>Финансовый результат WB</span><strong>{money(data.accounting_profit_rub)}</strong><small>с hybrid FIFO-себестоимостью и оценкой неполных строк</small></div>
          <div className="stat"><span>Подтверждённые внешние расходы</span><strong>{money(data.confirmed_external_expenses_rub)}</strong><small>{data.expenses.included.length} строк включено в расчёт</small></div>
        </div>
        <div className="cash-grid management-pnl-exclusions">
          <div className="stat"><span>Не подтверждены</span><strong>{money(data.excluded_unconfirmed_rub)}</strong></div>
          <div className="stat"><span>Комиссия WB исключена</span><strong>{money(data.excluded_wb_commission_rub)}</strong><small>уже учтена в результате WB</small></div>
          <div className="stat"><span>Неизвестный источник / основа</span><strong>{money(data.unknown_source_rub)}</strong><small>{data.expenses.unknown_source.length} строк не вычтено · {data.unknown_allocation_count} без суммы распределения</small></div>
        </div>
        <p className="form-hint management-pnl-attestation">Подтверждение — отметка пользователя, а не автоматическая проверка. Проверьте период и основу распределения расхода (всего, на единицу или процент выручки), а также что сумма не задвоена с данными WB, рекламой и себестоимостью.</p>
        <ExpenseLines title="Включено в результат" items={data.expenses.included} />
        <ExpenseLines title="Исключено" items={data.expenses.excluded} />
        <ExpenseLines title="Источник или основа не распознаны; не вычтено" items={data.expenses.unknown_source} />
      </>}
    </> : null}
  </section>;
}
