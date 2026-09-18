import { useState } from "react";
import { createRoot } from "react-dom/client";
import { OnboardingPanel } from "../../src/OnboardingPanel";
import type { ActivationSummary, ProductCostSetting, TenantSyncRun } from "../../src/data-source";
import "../../src/styles.css";
import "./style.css";

const scenario = new URLSearchParams(location.search).get("case") ?? "success";
let testCost = scenario === "zero" ? 0 : 420;
let acknowledged = false;
const fullRun: TenantSyncRun = { id: 1, started_at: "2026-09-18T10:00:00", finished_at: "2026-09-18T10:01:00", status: scenario === "error" ? "error" : "success", details: "orders: 2, sales: 2, financial_rows: 2" };
const report = { nm_id: 123, product: "Учебный набор для творчества", period: { start: "2026-08-19", end: "2026-09-17" }, revenue: 2400, sales: 2, unit_cost: 420, margin: 1100, margin_pct: 45.8, limitation: "Вымышленные данные стенда. Налоги и неизвестные внешние расходы не включены.", report_revision: "a".repeat(64) };

// Isolated local test transport. There is deliberately no network fallback.
// The real product component is reused; all data and acknowledgements stay here.
window.fetch = async (input, init) => {
  const url = new URL(typeof input === "string" ? input : input instanceof URL ? input.href : input.url, location.origin);
  const nmId = Number(url.searchParams.get("nm_id")) || null;
  if (url.pathname.endsWith("/api/v1/growth/activation")) {
    const complete = fullRun.status === "success";
    const result: ActivationSummary = {
      schema_version: 1, is_demo: false, wb_connected: true,
      sync: { state: fullRun.status, complete, successful_full_sync_at: complete ? fullRun.finished_at : null },
      selected_product: { nm_id: nmId, cost_configured: Boolean(nmId && testCost > 0), has_financial_rows: Boolean(nmId) },
      ready_for_report: complete && Boolean(nmId) && testCost > 0,
      next_step: !complete ? "sync" : !nmId ? "select_product" : testCost <= 0 ? "cost" : "open_report",
      first_value_verified: acknowledged, first_value_at: acknowledged ? "2026-09-18T11:00:00+00:00" : null,
    };
    return Response.json(result);
  }
  if (url.pathname.endsWith("/api/v1/growth/first-report")) {
    if (fullRun.status !== "success" || testCost <= 0 || nmId !== 123) return Response.json({ detail: "Тестовый отчёт пока не готов" }, { status: 409 });
    return Response.json({ ...report, unit_cost: testCost });
  }
  if (url.pathname.endsWith("/api/v1/growth/report-viewed") && init?.method === "POST") {
    acknowledged = true;
    return Response.json({ first_value_verified: true, first_value_at: "2026-09-18T11:00:00+00:00" });
  }
  throw new Error(`Стенд заблокировал незапланированный запрос: ${url.pathname}`);
};

function Fixture() {
  const [costs, setCosts] = useState<ProductCostSetting[]>([{ nm_id: 123, supplier_article: "fixture-123", product_name: report.product, cost_per_wb_unit: testCost, updated_at: "2026-09-18T10:02:00", note: "Только тест" }]);
  const [runs, setRuns] = useState([fullRun]);
  const [days, setDays] = useState(30);
  return <main className="qa-fixture">
    <header className="qa-notice"><strong>ТЕСТОВЫЙ СТЕНД · ДАННЫЕ ВЫМЫШЛЕННЫЕ</strong><p>Реальный компонент onboarding с локальной имитацией API. WB, кабинет и аналитика не вызываются, события никуда не отправляются.</p><nav aria-label="Тестовые сценарии"><a href="?case=error">Ошибка загрузки</a><a href="?case=zero">Нулевая себестоимость</a><a href="?case=success">Готовый первый отчёт</a></nav></header>
    <label className="qa-cost">Тестовая себестоимость, ₽<input id="qa-cost" type="number" min="0" value={costs[0].cost_per_wb_unit} onChange={(event) => { testCost = Number(event.target.value); setCosts([{ ...costs[0], cost_per_wb_unit: testCost }]); }} /></label>
    <OnboardingPanel connected runs={runs} costs={costs} days={days} busy={false} token="" onTokenChange={() => undefined} onConnect={() => undefined} onDaysChange={setDays} onSync={() => { fullRun.status = "success"; setRuns([{ ...fullRun }]); }} onEditCost={() => document.getElementById("qa-cost")?.focus()} />
  </main>;
}

createRoot(document.getElementById("root")!).render(<Fixture />);
