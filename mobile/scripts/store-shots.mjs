// Store assets: raw app frames (390x844 @3x, light theme, demo snapshot) -> composed marketing screenshots + Google Play feature graphic.
// Raw frames: Vite from ../mh-redesign-lab, /tenant-api/* answered from scripts/demo-cabinet.json via CDP Fetch (demo tenant, no customer data).
// Compose: store/compose/template.html rendered in headless Chrome.
//   cd ../mh-redesign-lab && npx vite --port 5360     # other terminal
// usage: node scripts/store-shots.mjs [http://127.0.0.1:5360/]   (env: ONLY=raw|compose, INSPECT=1 prints section layout)
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync, mkdirSync, readFileSync, existsSync } from "node:fs";
import { fileURLToPath, pathToFileURL } from "node:url";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2] ?? "http://127.0.0.1:5360/";
const EDGE = process.env.EDGE_PATH ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const CHROME = process.env.CHROME_PATH ?? "C:/Program Files/Google/Chrome/Application/chrome.exe";
// [file, ?view=, heading text to bring to the top of the frame, extra scrollY shift, optional workspace tab to open first]
// frame-only tidy-up of the monthly P&L table (data untouched): narrower label/padding so 2 months + total fit 390px, rows that are 0 in every column hidden
const PNL_FIT = `(${() => {
  document.head.appendChild(Object.assign(document.createElement("style"), { textContent: ".pnl-monthly-table{min-width:0!important}.pnl-monthly-table th,.pnl-monthly-table td{height:auto!important}.pnl-monthly-table tbody th{min-width:5.2rem!important;max-width:5.6rem!important;font-size:.72rem!important;padding:.4rem .2rem!important}.pnl-monthly-table th,.pnl-monthly-table td{padding:.4rem .2rem!important}.pnl-monthly-table td span{font-size:.74rem}.pnl-monthly-table td small{font-size:.62rem}.pnl-monthly-table thead th small{font-size:.58rem}" }));
  return [...document.querySelectorAll(".pnl-monthly-table tbody tr")].filter((r) => [...r.querySelectorAll("td span")].every((x) => /^[-−]?0\s?₽$/.test(x.textContent.trim()))).map((r) => (r.style.display = "none")).length;
}})()`;
// frame-only: hide blocks without real values (the only ads campaign has no name; reports KPI tiles/analytics are empty in the demo)
const HIDE_ADS = "(() => { const e = document.getElementById('ads-campaigns'); if (e) e.style.display = 'none'; return e ? 1 : 0; })()";
const HIDE_REPORTS = "(() => [...document.querySelectorAll('.metrics-grid, .report-analytics-section')].map((e) => (e.style.display = 'none')).length)()";
const PAGES = [["01-today", "today", "Очередь решений", -10, "Неделя"], ["02-finance", "finance", "Отчёт о прибылях и убытках по месяцам", -60, "Обзор", PNL_FIT], ["03-products", "products", "Товары по артикулам", -50], ["04-stock", "stock", "Остатки, маржа, сигнал", -50], ["05-ads", "ads", "Путь до заказа", -30, null, HIDE_ADS], ["06-reports", "reports", "Отчёты и расписания", -20, null, HIDE_REPORTS]];
const VIEWPORT = [390, 844, 3]; // raw 1170x2532
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
function today(period) {
  const t = structuredClone(DEMO[`dashboard/today@${period}`] ?? DEMO["dashboard/today"]); // real snapshot values only (zeros stay zeros; the frame scrolls to the real blocks)
  // the snapshot was cut at the start of a day: drop the trailing empty current day from the weekly series (real days only)
  for (const k of ["revenue_trend", "revenue_trend_week"]) { const a = t[k]; if (Array.isArray(a) && a.length && a.at(-1).revenue_rub === 0) a.pop(); }
  t.metrics = t.kpis;
  return t;
}
// /tenant-api path -> JSON body (null = 404, pages degrade as they do without that optional source)
function stub(path, period = "30d") {
  const pick = (key) => DEMO[`${key}@${period}`] ?? DEMO[key]; // period-specific snapshot first
  if (path === "/api/v1/dashboard/finance/wb-costs") return { deductions: [], measurement_penalties: [], paid_storage: [], paid_storage_status: "ok", errors: [] };
  // Optional WB sources absent from the snapshot: answer "empty" (not an error) so no "data unavailable" banner appears.
  if (path === "/api/v1/dashboard/customer-quality") return { rating: {}, unread: {}, feedbacks: [], questions: [], quarantine: [], returns: {}, return_history: [], chats: [] };
  if (path === "/api/v1/products/prices") return { items: [] };
  if (path === "/api/v1/products/cards") return { cards: [] };
  if (path === "/api/v1/action-plan/items") return { items: [] };
  if (path === "/api/v1/operations/reports") return { definitions: [], runs: [], metric_labels: {} };
  if (path === "/api/v1/operations/reports/delivery") return { configured: false, email_configured: false };
  if (path === "/api/v1/operations/incidents") return { cases: [], summary: {} };
  let m = path.match(/^\/api\/v1\/(?:dashboard|control)\/([a-z/-]+)$/);
  if (path === "/api/v1/dashboard/today") return today(period);
  if (path === "/api/v1/data-status") return { connected: true, state: "fresh", partial: false, updated_at: "2026-10-03T08:00:00Z", lag_minutes: 12, missing_costs: false, missing_material_inventory: false };
  if (m) { const v = pick(`${path.includes("/control/") ? "control" : "dashboard"}/${m[1]}`); return v && !v.__error__ ? v : null; }
  if (path === "/api/v1/marketplace-context/session") return { marketplace: "wb", role: "owner", can_write: false, is_demo: true, authorized: true, shared_routes: [] };
  if (path === "/api/v1/shop-context/session") return { shops: [{ id: 0, name: "Основной магазин", verified_identity: null, active: true, snapshot_revision: null, can_write: false, allowed_actions: [] }], role: "owner", selected_shop_id: 0, route_scopes: [] };
  if (path === "/api/products") { // legacy shape (English keys) built from the finance product table
    return { products: DEMO["dashboard/finance"].products.map((r) => ({ nm_id: r["Артикул WB"], article: r["Артикул продавца"], product: r["Товар"], orders: r["Продажи, шт"], sales: r["Продано нетто"], revenue: r["Продажи по отчёту"], buyout: r["Выкупы по цене покупателя"], ad_spend: r["Реклама"], drr: r["Доля рекламы, %"], stock: r["Остаток"], daily_sales: r["Продаж/день"], days_of_stock: r["Запас, дней"], unit_cost: r["Себестоимость ед."], margin: r["Чистая прибыль"], margin_pct: r["Чистая маржинальность, %"] })) };
  }
  if (path === "/api/overview") return overview();
  return null;
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const here = (p) => fileURLToPath(new URL(p, import.meta.url));

async function launch(exe) {
  const port = 10900 + Math.floor(Math.random() * 400);
  const proc = spawn(exe, ["--headless=new", "--disable-gpu", "--hide-scrollbars", "--force-color-profile=srgb", "--allow-file-access-from-files", `--remote-debugging-port=${port}`, `--user-data-dir=${mkdtempSync(join(tmpdir(), "mh-shots-"))}`, "about:blank"], { stdio: "ignore" });
  let tabs;
  for (let i = 0; i < 80 && !tabs; i++) { try { tabs = await (await fetch(`http://127.0.0.1:${port}/json`)).json(); } catch { await sleep(150); } }
  const ws = new WebSocket(tabs.find((t) => t.type === "page").webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r));
  let id = 1; const pend = new Map();
  ws.addEventListener("message", (e) => { const m = JSON.parse(e.data); pend.get(m.id)?.(m); });
  const send = (method, params = {}) => new Promise((res) => { const i = id++; pend.set(i, res); ws.send(JSON.stringify({ id: i, method, params })); });
  const ev = async (expression) => { const r = (await send("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true })).result; if (r.exceptionDetails) console.log("EVAL ERR", r.exceptionDetails.exception?.description?.slice(0, 200)); return r.result.value; };
  const shot = async () => Buffer.from((await send("Page.captureScreenshot", { format: "png" })).result.data, "base64");
  return { ws, send, ev, shot, close: () => { ws.close(); proc.kill(); } };
}

// page-side: section outline (INSPECT=1) to choose scroll targets
function outline() {
  const sel = "h1,h2,h3,nav,section,article,table";
  const sc = [...document.querySelectorAll("*")].filter((e) => e.scrollHeight > e.clientHeight + 50 && /auto|scroll/.test(getComputedStyle(e).overflowY)).map((e) => e.tagName + "." + e.className).join(",");
  return "docH=" + document.documentElement.scrollHeight + " scrollers=" + sc + " ## " + [...document.querySelectorAll(sel)].filter((e) => e.getBoundingClientRect().height > 20).slice(0, 90)
    .map((e) => e.tagName + "." + String(e.className).slice(0, 28) + "@" + Math.round(e.getBoundingClientRect().top + scrollY) + ":" + Math.round(e.getBoundingClientRect().height) + " " + e.innerText.slice(0, 32).replace(/\s+/g, "|")).join(" ## ");
}

// ---- stage 1: raw app frames ------------------------------------------------------------------------------
async function raw() {
  const b = await launch(EDGE);
  const { send, ev } = b;
  b.ws.addEventListener("message", (e) => {
    const m = JSON.parse(e.data);
    if (m.method !== "Fetch.requestPaused") return;
    const { requestId, request } = m.params;
    const u = new URL(request.url);
    const body = stub(u.pathname.replace(/^\/tenant-api/, ""), u.searchParams.get("period") ?? "30d");
    send("Fetch.fulfillRequest", { requestId, responseCode: body ? 200 : 404, responseHeaders: [{ name: "Content-Type", value: "application/json" }], body: Buffer.from(JSON.stringify(body ?? { detail: "demo snapshot: not provided" })).toString("base64") });
  });
  try {
    const [w, h, dpr] = VIEWPORT;
    mkdirSync(here("../store/screenshots/raw"), { recursive: true });
    await send("Page.enable"); await send("Network.enable"); await send("Network.setBypassServiceWorker", { bypass: true }); await send("Fetch.enable", { patterns: [{ urlPattern: "*/tenant-api/*" }] });
    await send("Page.navigate", { url: BASE }); await sleep(1000);
    await ev(`localStorage.setItem('marketshelper-theme','light')`); // index.html defaults to dark otherwise
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: dpr, mobile: true });
    await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
    for (const [name, view, text, shift, tab, prep] of PAGES) {
      await send("Page.navigate", { url: `${BASE}?view=${view}&period=${view === "finance" ? "30d" : "90d"}` });
      await sleep(3500);
      await ev(`document.head.appendChild(Object.assign(document.createElement('style'),{textContent:'.connection-banner,.marketplace-bar,.shop-context-bar,.mock-banner,.bottom-nav{display:none!important}#root{padding-bottom:500px!important}'}))`);
      if ((await ev(`document.documentElement.dataset.theme`)) !== "light") throw new Error("expected light theme");
      if (tab) { await ev(`(async () => { for (const t of ${JSON.stringify([].concat(tab))}) { [...document.querySelectorAll('button,a,[role=tab]')].find((e) => e.textContent.trim().startsWith(t))?.click(); await new Promise((r) => setTimeout(r, 900)); } })()`); await sleep(1200); }
      if (prep) { await sleep(2500); if (process.env.DBG) console.log("DBG", await ev(`JSON.stringify([[document.querySelector(".pnl-monthly-scroll").clientWidth, document.querySelector(".pnl-monthly-table").offsetWidth, [...document.querySelectorAll(".pnl-monthly-table thead th")].map(t=>t.offsetWidth)],[...document.querySelectorAll(".pnl-monthly-table tbody tr:nth-child(2) td span")].map(x=>[...x.textContent].map(c=>c.charCodeAt(0)).join(",")),document.querySelectorAll(".pnl-monthly-table").length, [...document.querySelectorAll(".pnl-monthly-table tbody tr")].slice(0,3).map(r=>r.innerText)])`)); console.log("prep hid", await ev(prep)); if (process.env.DBG) console.log("DBG2", await ev(`JSON.stringify([document.querySelector(".pnl-monthly-scroll").clientWidth, document.querySelector(".pnl-monthly-table").offsetWidth, [...document.querySelectorAll(".pnl-monthly-table thead th")].map(t=>t.offsetWidth)])`)); await sleep(300); }
      if (process.env.INSPECT) console.log(name, (await ev(`(${outline})()`)).split(" ## ").join(String.fromCharCode(10)));
      if (process.env.FULL) { const H = await ev("document.documentElement.scrollHeight"); await send("Emulation.setDeviceMetricsOverride", { width: w, height: H, deviceScaleFactor: 1, mobile: true }); await sleep(500); writeFileSync(process.env.TEMP + "/full-" + name + ".png", await b.shot()); await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: dpr, mobile: true }); continue; }
      // scroll: bring the heading containing `text` (or a numeric scrollY) to just under the top edge
      const y = await ev(`(() => { const t = ${JSON.stringify(text)}; if (typeof t === 'number') return t; if (!t) return 0;
        const el = [...document.querySelectorAll('h1,h2,h3,h4,strong,b,span,p,div')].find((e) => e.children.length === 0 && e.textContent.toLowerCase().includes(t.toLowerCase()));
        return el ? el.getBoundingClientRect().top + scrollY - 20 : 0; })()`);
      await ev(`window.scrollTo(0, ${y} + ${shift})`);
      await sleep(600);
      writeFileSync(here(`../store/screenshots/raw/${name}.png`), await b.shot());
      console.log("raw", name, `${w * dpr}x${h * dpr}`, "scrollY", await ev("Math.round(scrollY)"));
    }
  } finally { b.close(); }
}

// ---- stage 2: composition (store/compose/template.html in Chrome) --------------------------------------------
const SHOTS = [
  ["01-today", "Главное за день|на одном экране", "Выручка, остатки и что требует действия"],
  ["02-finance", "Куда уходят деньги|и что остаётся", "ОПиУ по месяцам: выручка, расходы, результат"],
  ["03-products", "Маржа|по каждому товару", "Прибыль, расходы и рентабельность по артикулам"],
  ["04-stock", "Остатки и что|везти на склад", "Запас в днях и покрытие по каждому товару"],
  ["05-ads", "Реклама: где окупается,|где нет", "Воронка до заказа и ДРР по артикулам"],
  ["06-reports", "Готовые отчёты|в PDF в один клик", "Сводки за 1, 7, 30 и 90 дней"],
];
const TARGETS = { android: [1080, 1920], ios: [1290, 2796] };
async function compose() {
  const b = await launch(existsSync(CHROME) ? CHROME : EDGE);
  const tpl = pathToFileURL(here("../store/compose/template.html")).href;
  const out = (p) => here(`../store/screenshots/${p}`);
  try {
    await b.send("Page.enable");
    const render = async (w, h, data, file) => {
      await b.send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: 1, mobile: false });
      await b.send("Page.navigate", { url: tpl + "#" + encodeURIComponent(JSON.stringify(data)) });
      await sleep(300);
      await b.ev(`document.fonts.ready.then(() => Promise.all([...document.images].map((i) => i.decode().catch(() => 0)))).then(() => 1)`);
      await b.ev(`location.reload()`); await sleep(700); // hash set before load: ensure a clean render
      await b.ev(`document.fonts.ready.then(() => Promise.all([...document.images].map((i) => i.decode().catch(() => 0)))).then(() => 1)`);
      writeFileSync(file, await b.shot());
      console.log("compose", file.split(/[\/]/).slice(-2).join("/"), `${w}x${h}`);
    };
    for (const [dev, [w, h]] of Object.entries(TARGETS)) {
      mkdirSync(out(dev), { recursive: true });
      for (const [name, title, sub] of SHOTS) await render(w, h, { kind: "shot", title, sub, img: pathToFileURL(out(`raw/${name}.png`)).href }, out(`${dev}/${name}.png`));
    }
    await render(1024, 500, { kind: "feature", title: "Учёт себестоимости, остатков и маржи|для селлеров WB и Ozon", logo: pathToFileURL(here("../../mh-redesign-lab/public/icons/marketshelper.svg")).href }, out("feature-graphic-1024x500.png"));
  } finally { b.close(); }
}

if (process.env.ONLY !== "compose") await raw();
if (process.env.ONLY !== "raw" && !process.env.INSPECT) await compose();
