// Store screenshots from the Vite mock mode (demo data only): VITE_MH_API_MODE=mock npx vite --port 5350
// usage: node scripts/store-shots.mjs [http://127.0.0.1:5350/]
import { spawn } from "node:child_process";
import { mkdtempSync, writeFileSync, mkdirSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const BASE = process.argv[2] ?? "http://127.0.0.1:5350/";
const EDGE = process.env.EDGE_PATH ?? "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe";
const PAGES = [["01-today", "today"], ["02-finance", "finance"], ["03-products", "products"], ["04-stock", "stock"], ["05-ads", "ads"], ["06-reports", "reports"]];
const DEVICES = { android: [360, 640, 3], ios: [430, 932, 3] }; // 1080x1920, 1290x2796
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
const ev = (expression) => send("Runtime.evaluate", { expression, returnByValue: true });

try {
  for (const [dev, [w, h, dpr]] of Object.entries(DEVICES)) {
    mkdirSync(new URL(`../store/screenshots/${dev}`, import.meta.url), { recursive: true });
    await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: dpr, mobile: true });
    await send("Emulation.setTouchEmulationEnabled", { enabled: true, maxTouchPoints: 5 });
    for (const [name, view] of PAGES) {
      await send("Page.navigate", { url: `${BASE}?view=${view}` });
      await sleep(500);
      await sleep(3000);
      // The cabinet UI is dark-only (no light theme in mh-redesign-lab); hide the mock-mode banner for store shots.
      await ev(`document.head.appendChild(Object.assign(document.createElement('style'),{textContent:'.data-banner{display:none!important}'}))`);
      await sleep(300);
      const shot = await send("Page.captureScreenshot", { format: "png" });
      const file = new URL(`../store/screenshots/${dev}/${name}.png`, import.meta.url);
      writeFileSync(file, Buffer.from(shot.result.data, "base64"));
      console.log(dev, name, w * dpr + "x" + h * dpr);
    }
  }
} finally { ws.close(); proc.kill(); }
