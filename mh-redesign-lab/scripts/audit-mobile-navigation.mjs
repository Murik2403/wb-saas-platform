import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const edgePath = process.env.EDGE_PATH ?? "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const url = process.argv[2] ?? "http://127.0.0.1:5177/?view=today";
const port = 10000 + Math.floor(Math.random() * 500);
const profile = mkdtempSync(join(tmpdir(), "mh-mobile-navigation-"));
const browser = spawn(edgePath, [
  "--headless=new",
  "--disable-gpu",
  "--hide-scrollbars",
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`,
  "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
const withTimeout = (promise, label) => Promise.race([
  promise,
  new Promise((_, reject) => setTimeout(() => reject(new Error(`Timed out: ${label}`)), 8000)),
]);

async function getJson(path) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}${path}`);
      if (response.ok) return response.json();
    } catch {
      // The browser is still starting.
    }
    await sleep(120);
  }
  throw new Error("Unable to connect to Edge debugging endpoint");
}

function connect(wsUrl) {
  const socket = new WebSocket(wsUrl);
  const pending = new Map();
  let nextId = 1;
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    pending.get(message.id)(message);
    pending.delete(message.id);
  });
  return new Promise((resolve) => socket.addEventListener("open", () => resolve({
    send(method, params = {}) {
      const id = nextId;
      nextId += 1;
      socket.send(JSON.stringify({ id, method, params }));
      return withTimeout(new Promise((done) => pending.set(id, done)), method);
    },
    close: () => socket.close(),
  })));
}

const failures = [];

try {
  const tabs = await getJson("/json");
  const page = tabs.find((tab) => tab.type === "page");
  const client = await connect(page.webSocketDebuggerUrl);
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  await client.send("Emulation.setDeviceMetricsOverride", {
    width: 390,
    height: 844,
    deviceScaleFactor: 1,
    mobile: true,
  });
  await client.send("Page.navigate", { url });
  await sleep(1200);

  const evaluate = async (expression) => {
    const response = await client.send("Runtime.evaluate", { expression, returnByValue: true });
    const result = response.result.result;
    if (result.subtype === "error") throw new Error(result.description);
    return result.value;
  };

  const initial = await evaluate(`(() => ({
    menuButton: Boolean(document.querySelector('button[aria-label="Открыть меню"]')),
    bottomNav: Array.from(document.querySelectorAll('.bottom-nav button')).map((button) => button.textContent?.trim()),
    width: document.documentElement.scrollWidth,
    viewport: innerWidth,
  }))()`);
  if (!initial.menuButton) failures.push("mobile menu trigger is missing");
  if (initial.width > initial.viewport + 1) failures.push("initial page has horizontal overflow");
  for (const label of ["Главная", "Склад", "Производство", "Поставки"]) {
    if (!initial.bottomNav.includes(label)) failures.push(`bottom navigation is missing ${label}`);
  }

  await evaluate(`document.querySelector('button[aria-label="Открыть меню"]')?.click()`);
  await sleep(150);
  const menu = await evaluate(`(() => ({
    open: Boolean(document.querySelector('.mobile-menu')),
    labels: Array.from(document.querySelectorAll('.mobile-menu .nav-item')).map((button) => button.textContent?.trim()),
  }))()`);
  if (!menu.open) failures.push("mobile menu does not open");
  if (menu.labels.length !== 11) failures.push(`mobile menu has ${menu.labels.length} sections instead of 11`);

  await evaluate(`Array.from(document.querySelectorAll('.mobile-menu .nav-item')).find((button) => button.textContent?.includes('Отчеты'))?.click()`);
  await sleep(150);
  const reports = await evaluate(`({ view: new URL(location.href).searchParams.get('view'), section: document.querySelector('.topbar-title span')?.textContent?.trim(), menuOpen: Boolean(document.querySelector('.mobile-menu')) })`);
  if (reports.view !== "reports" || reports.section !== "Отчеты") failures.push("mobile menu does not navigate to reports");
  if (reports.menuOpen) failures.push("mobile menu remains open after navigation");

  await evaluate(`Array.from(document.querySelectorAll('.bottom-nav button')).find((button) => button.textContent?.includes('Производство'))?.click()`);
  await sleep(150);
  const production = await evaluate(`({ view: new URL(location.href).searchParams.get('view'), section: document.querySelector('.topbar-title span')?.textContent?.trim() })`);
  if (production.view !== "production" || production.section !== "Производство") failures.push("bottom navigation does not navigate to production");

  const hasPlan = await evaluate(`Boolean(document.querySelector('.plan-counter'))`);
  if (hasPlan) {
    await evaluate(`document.querySelector('.plan-counter')?.click()`);
    await sleep(100);
    const drawer = await evaluate(`Boolean(document.querySelector('.plan-drawer'))`);
    if (!drawer) failures.push("task plan does not open");
    await evaluate(`document.querySelector('.drawer-close-button')?.click()`);
    await sleep(100);
    const closed = await evaluate(`!document.querySelector('.plan-drawer')`);
    if (!closed) failures.push("task plan does not close");
  }

  console.log(JSON.stringify({ checked: "mobile-navigation", initial, menu, reports, production, hasPlan, failures }, null, 2));
  if (failures.length) process.exitCode = 1;
  client.close();
} finally {
  try { browser.kill(); } catch { /* already stopped */ }
  setTimeout(() => {
    try { rmSync(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 }); } catch { /* Windows can keep the profile briefly */ }
  }, 300);
}
