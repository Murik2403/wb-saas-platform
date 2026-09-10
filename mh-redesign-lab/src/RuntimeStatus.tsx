import { useEffect, useState } from "react";
import { getRuntimeStatus } from "./data-source";
import { historyDate } from "./history-format";

const names: Record<string, string> = {backup: "Резервные копии", sync: "Импорт WB", recommendations: "Рекомендации", reports: "Отчёты", products: "Товары", orders: "Заказы", sales: "Продажи", financial_rows: "Финансы", stocks: "Остатки", ads: "Реклама"};
const states: Record<string, string> = {waiting: "Ожидает расписания", running: "Выполняется", success: "Выполнено", error: "Ошибка", owned_by_other_process: "Работает в другом процессе"};

export function RuntimeStatus() {
  const [data, setData] = useState<Awaited<ReturnType<typeof getRuntimeStatus>> | null>(null);
  const [error, setError] = useState(false);
  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    const refresh = async () => {
      if (pending) return;
      pending = true;
      try { const next = await getRuntimeStatus(controller.signal); if (!controller.signal.aborted) { setData(next); setError(false); } }
      catch { if (!controller.signal.aborted) setError(true); }
      finally { pending = false; }
    };
    void refresh();
    const timer = window.setInterval(() => void refresh(), 15000);
    return () => { controller.abort(); window.clearInterval(timer); };
  }, []);
  return <div className="runtime-status">
    <div><h3>Фоновые задачи</h3>{error ? <p>Не удалось проверить состояние задач.</p> : !data ? <p>Проверяем…</p> : !data[0].enabled ? <p>Локальный планировщик выключен. В контейнере задачи обслуживаются отдельными процессами.</p> : Object.entries(data[0].jobs).map(([name, job]) => <p key={name}>{names[name] ?? name}: {states[job.status] ?? job.status}</p>)}<small>Локальные отчёты сохраняются без автоматической отправки. Рекомендации не применяются автоматически.</small></div>
    <div><h3>Последнее обновление разделов</h3>{data && Object.entries(data[1].sections).map(([name, value]) => <p key={name}>{names[name]}: {value ? historyDate(value) : "Нет подтверждённого обновления"}</p>)}</div>
  </div>;
}
