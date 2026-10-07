// Store screenshots of the light-theme cabinet filled from scripts/demo-cabinet.json (demo tenant snapshot, no customer data).
// Vite runs in the default API mode; /tenant-api/* is answered from the snapshot through CDP Fetch interception
// (VITE_MH_API_MODE=mock never calls the API, so it would show the empty built-in mock).
//   cd ../mh-redesign-lab && npx vite --port 5360     # other terminal
// usage: node scripts/store-shots.mjs [http://127.0.0.1:5360/]
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync, mkdirSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2] ?? "http://127.0.0.1:5360/";
const EDGE = process.env.EDGE_PATH ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
// [file, ?view=, CSS anchor to scroll to so the shot starts at content rather than the search/toolbar rows]
const PAGES = [["01-today", "today", ".workspace-view"], ["02-finance", "finance", ".metrics-grid"], ["03-products", "products", ".metrics-grid"], ["04-stock", "stock", ".metrics-grid"], ["05-ads", "ads", ".workspace-view"], ["06-reports", "reports", ".workspace-view"]];
const DEVICES = { android: [360, 640, 3], ios: [430, 932, 3] }; // 1080x1920, 1290x2796
const DEMO = JSON.parse(readFileSync(new URL("./demo-cabinet.json", import.meta.url), "utf8"));
const rub = (v) => Math.round(v).toLocaleString("ru-RU") + " ₽";
function overview() {
  const f = DEMO["dashboard/finance"].financial;
  const kpi = (key, label, value, note) => ({ key, label, value, note, tone: "info" });
  return {
    period_label: DEMO["dashboard/finance"].period.label, start: DEMO["dashboard/finance"].period.start, end: DEMO["dashboard/finance"].period.end,
    kpis: [kpi("profit", "Прибыль", rub(f.profit), "расчётная по товарам"), kpi("revenue", "Выручка", rub(f.sales), "продажи по отчёту"),
      kpi("ad_spend", "Реклама", rub(f.financial_ad_spend), "расход за период"),
      kpi("stock", "Остаток WB", String(DEMO["dashboard/stock"].metrics[0]?.value ?? 0), "на складах"),
      kpi("net_margin_pct", "Расчётная маржинальность", (100 * f.profit / f.sales).toFixed(1) + " %", "по учтённым затратам"),
      kpi("orders_profitability_pct", "Рентабельность заказов", (100 * f.profit / (f.gross_sales || f.sales)).toFixed(1) + " %", "от суммы заказов")],
    products: [], problem_products: [], alerts: [], cash_windows: [],
  };
}
// The snapshot was cut at the start of a day, so "today" is empty. For the store shots "today" is filled with the
// previous day's revenue from the same snapshot (orders = revenue / average unit price, ads at the period's DRR).
function today() {
  const t = structuredClone(DEMO["dashboard/today"]);
  const f = DEMO["dashboard/finance"].financial;
  const week = t.revenue_trend;
  const rev = week[week.length - 2].revenue_rub;
  const orders = Math.round(rev / (f.sales / f.sale_units));
  const drr = (100 * f.financial_ad_spend) / f.sales;
  week[week.length - 1].revenue_rub = rev;
  const set = (key, value, note) => Object.assign(t.kpis.find((k) => k.key === key), { value, note });
  set("orders", String(orders), rub(rev));
  set("sales", String(Math.round(orders * 0.92)), rub(rev * 0.92));
  set("ads", rub((rev * drr) / 100), `ДРР ${drr.toFixed(1)}%`);
  set("shift", "3/4", "Смена в работе");
  t.metrics = t.kpis;
  return t;
}
// /tenant-api path -> JSON body (null = 404, pages degrade as they do without that optional source)
function stub(path) {
  // Optional WB sources absent from the snapshot: answer "empty" (not an error) so no "data unavailable" banner appears.
  if (path === "/api/v1/dashboard/customer-quality") return { rating: {}, unread: {}, feedbacks: [], questions: [], quarantine: [], returns: {}, return_history: [], chats: [] };
  if (path === "/api/v1/products/prices") return { items: [] };
  if (path === "/api/v1/products/cards") return { cards: [] };
  if (path === "/api/v1/action-plan/items") return { items: [] };
  if (path === "/api/v1/operations/reports") return { definitions: [], runs: [], metric_labels: {} };
  if (path === "/api/v1/operations/reports/delivery") return { configured: false, email_configured: false };
  if (path === "/api/v1/operations/incidents") return { cases: [], summary: {} };
  let m = path.match(/^\/api\/v1\/(?:dashboard|control)\/([a-z-]+)$/);
  if (path === "/api/v1/dashboard/today") return today();
  if (path === "/api/v1/data-status") return { connected: true, state: "fresh", partial: false, updated_at: "2026-10-03T08:00:00Z", lag_minutes: 12, missing_costs: false, missing_material_inventory: false };
  if (m) { const v = DEMO[`${path.includes("/control/") ? "control" : "dashboard"}/${m[1]}`]; return v && !v.__error__ ? v : null; }
  if (path === "/api/v1/marketplace-context/session") return { marketplace: "wb", role: "owner", can_write: false, is_demo: true, authorized: true, shared_routes: [] };
  if (path === "/api/v1/shop-context/session") return { shops: [{ id: 0, name: "Основной магазин", verified_identity: null, active: true, snapshot_revision: null, can_write: false, allowed_actions: [] }], role: "owner", selected_shop_id: 0, route_scopes: [] };
  if (path === "/api/products") { // legacy shape (English keys) built from the finance product table
    return { products: DEMO["dashboard/finance"].products.map((r) => ({ nm_id: r["Артикул WB"], article: r["Артикул продавца"], product: r["Товар"], orders: r["Продажи, шт"], sales: r["Продано нетто"], revenue: r["Продажи по отчёту"], buyout: r["Выкупы по цене покупателя"], ad_spend: r["Реклама"], drr: r["Доля рекламы, %"], stock: r["Остаток"], daily_sales: r["Продаж/день"], days_of_stock: r["Запас, дней"], unit_cost: r["Себестоимость ед."], margin: r["Чистая прибыль"], margin_pct: r["Чистая маржинальность, %"] })) };
  }
  if (path === "/api/overview") return overview();
  return null;
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const port = 10900 + Math.floor(Math.random() * 400);
const proc = spawn(EDGE, ["--headless=new", "--disable-gpu", "--hide-scrollbars", `--remote-debugging-port=${port}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "mh-shots-"))}`, "about:blank"], { stdio: "ignore" });
let tabs;
for (let i = 0; i < 80 && !tabs; i++) { try { tabs = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); } catch { await sleep(150); } }
const ws = new WebSocket(tabs.find((t) => t.type === "page").webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let id = 1; const pend = new Map();
ws.addEventListener("message", (e) => { const m = JSON.parse(e.data); pend.get(m.id)?.(m); });
const send = (method, params = {}) => new Promise((res) => { const i = id++; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
ws.addEventListener("message", (e) => {
  const m = JSON.parse(e.data);
  if (m.method !== "Fetch.requestPaused") return;
  const { requestId, request } = m.params;
  const body = stub(new URL(request.url).pathname.replace(/^\/tenant-api/, ""));
  if (process.env.DUMP && !body) console.log("  404", request.url);
  send("Fetch.fulfillRequest", { requestId, responseCode: body ? 200 : 404, responseHeaders: [{ name: "Content-Type", value: "application/json" }], body: Buffer.from(JSON.stringify(body ?? { detail: "demo snapshot: not provided" })).toString("base64") });
});
const ev = (expression) => send("Runtime.evaluate", { expression, returnByValue: true });

try {
  await send("Page.enable"); await send("Network.enable"); await send("Network.setBypassServiceWorker", { bypass: true }); await send("Fetch.enable", { patterns: [{ urlPattern: "*/tenant-api/*" }] });
  await send("Page.navigate", { url: BASE }); await sleep(1000);
  await ev(`localStorage.setItem('marketshelper-theme','light')`); // index.html defaults to dark otherwise
  for (const [dev, [w, h, dpr]] of Object.entries(DEVICES).slice(0, process.env.DUMP ? 1 : 9)) {
    mkdirSync(new URL(`../store/screenshots/${dev}`, import.meta.url), { recursive: true });
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: dpr, mobile: true });
    await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
    for (const [name, view, anchor] of PAGES) {
      await send("Page.navigate", { url: `${BASE}?view=${view}` });
      await sleep(500);
      await sleep(3000);
      await ev(`document.head.appendChild(Object.assign(document.createElement('style'),{textContent:'.connection-banner,.marketplace-bar,.shop-context-bar{display:none!important}'}))`);
      const theme = (await ev(`document.documentElement.dataset.theme`)).result.result.value;
      if (theme !== "light") throw new Error("expected light theme, got " + theme);
      await ev(`window.scrollTo(0, document.querySelector('${anchor}')?.getBoundingClientRect().top + scrollY - 12 || 0)`); // start at the content, not the search/toolbar rows
      if (process.env.DUMP) console.log(name, JSON.stringify((await ev(`[...document.querySelectorAll('main > *, main > * > *')].slice(0,40).map(e=>e.tagName+'.'+e.className+'@'+Math.round(e.getBoundingClientRect().top+scrollY)+':'+Math.round(e.getBoundingClientRect().height))`)).result.result.value));
      await sleep(300);
      const shot = await send("Page.captureScreenshot", { format: "png" });
      const file = new URL(`../store/screenshots/${dev}/${name}.png`, import.meta.url);
      writeFileSync(file, Buffer.from(shot.result.data, "base64"));
      console.log(dev, name, w * dpr + "x" + h * dpr);
    }
  }
} finally { ws.close(); proc.kill(); }
