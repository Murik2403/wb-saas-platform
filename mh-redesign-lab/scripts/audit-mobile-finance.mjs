import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const edgePath = process.env.EDGE_PATH ?? "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const url = process.argv[2] ?? "http://127.0.0.1:5177/?view=finance&period=7d";
const port = 9900 + Math.floor(Math.random() * 80);
const profile = mkdtempSync(join(tmpdir(), "mh-mobile-finance-"));
const browser = spawn(edgePath, [
  "--headless=new",
  "--disable-gpu",
  "--hide-scrollbars",
  `--remote-debugging-port=${port}`,
  `--user-data-dir=${profile}`,
  "about:blank",
], { stdio: "ignore" });

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function getJson(path) {
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(`http://127.0.0.1:${port}${path}`);
      if (response.ok) return response.json();
    } catch {
      // Browser is still starting.
    }
    await sleep(120);
  }
  throw new Error("Unable to connect to Edge debugging endpoint");
}

function connect(wsUrl) {
  const socket = new WebSocket(wsUrl);
  let nextId = 1;
  const pending = new Map();
  socket.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (!message.id || !pending.has(message.id)) return;
    pending.get(message.id)(message);
    pending.delete(message.id);
  });
  return new Promise((resolve) => socket.addEventListener("open", () => resolve({
    send(method, params = {}) {
      const id = nextId++;
      socket.send(JSON.stringify({ id, method, params }));
      return new Promise((done) => pending.set(id, done));
    },
    close: () => socket.close(),
  })));
}

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
  await sleep(5000);
  const evaluation = await client.send("Runtime.evaluate", {
    returnByValue: true,
    expression: `(() => {
      const control = document.querySelector('.period-control');
      const header = control?.closest('.section-header');
      const heading = header?.querySelector(':scope > div:first-child');
      const buttons = Array.from(control?.querySelectorAll('.period-segments button') ?? []);
      const rowTops = [...new Set(buttons.map((button) => Math.round(button.getBoundingClientRect().top)))];
      const headingRect = heading?.getBoundingClientRect();
      const controlRect = control?.getBoundingClientRect();
      const nav = document.querySelector('.bottom-nav');
      const navRect = nav?.getBoundingClientRect();
      const workspace = document.querySelector('.workspace');
      const workspaceStyle = workspace ? getComputedStyle(workspace) : null;
      return {
        rowCount: rowTops.length,
        headerSeparated: Boolean(headingRect && controlRect && headingRect.bottom <= controlRect.top + 1),
        documentWidth: document.documentElement.scrollWidth,
        viewportWidth: innerWidth,
        bottomPadding: Number.parseFloat(workspaceStyle?.paddingBottom ?? '0'),
        navClearance: navRect ? innerHeight - navRect.top : 0,
      };
    })()`,
  });
  const result = evaluation.result.result.value;
  const failures = [];
  if (result.rowCount !== 1) failures.push(`period selector uses ${result.rowCount} rows`);
  if (!result.headerSeparated) failures.push("period selector shares or overlaps the heading row");
  if (result.documentWidth > result.viewportWidth + 1) failures.push("page has horizontal overflow");
  if (result.bottomPadding < result.navClearance) failures.push("content can finish behind the bottom navigation");
  console.log(JSON.stringify({ result, failures }, null, 2));
  if (failures.length) process.exitCode = 1;
  client.close();
} finally {
  try { browser.kill(); } catch { /* already stopped */ }
  setTimeout(() => {
    try { rmSync(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 }); } catch { /* Windows lock */ }
  }, 300);
}
