import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const edgePath =
  process.env.EDGE_PATH ?? "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const baseUrl = process.argv[2] ?? "http://127.0.0.1:5177/";
const waitMs = Number(process.argv[3] ?? 900);
const allViews = [
  "today",
  "stock",
  "ads",
  "finance",
  "production",
  "supply",
  "assistant",
  "reports",
  "products",
  "control",
  "settings",
];
const views = process.argv[4] ? process.argv[4].split(",") : allViews;
const sizes = [
  { name: "phone", width: 390, height: 844, mobile: true },
  { name: "desktop", width: 1440, height: 1000, mobile: false },
];
const port = 9700 + Math.floor(Math.random() * 200);
const profile = mkdtempSync(join(tmpdir(), "mh-pages-"));

const browser = spawn(
  edgePath,
  [
    "--headless=new",
    "--disable-gpu",
    "--hide-scrollbars",
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    "--window-size=1440,1000",
    "about:blank",
  ],
  { stdio: "ignore" },
);

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

async function getJson(path) {
  const endpoint = `http://127.0.0.1:${port}${path}`;
  for (let attempt = 0; attempt < 50; attempt += 1) {
    try {
      const response = await fetch(endpoint);
      if (response.ok) return response.json();
    } catch {
      // Browser is still starting.
    }
    await sleep(120);
  }
  throw new Error(`Unable to reach ${endpoint}`);
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
  return new Promise((resolve) => {
    socket.addEventListener("open", () =>
      resolve({
        send(method, params = {}) {
          const id = nextId++;
          socket.send(JSON.stringify({ id, method, params }));
          return new Promise((done) => pending.set(id, done));
        },
        close: () => socket.close(),
      }),
    );
  });
}

const failures = [];
const rows = [];

try {
  const tabs = await getJson("/json");
  const page = tabs.find((tab) => tab.type === "page");
  const client = await connect(page.webSocketDebuggerUrl);
  await client.send("Page.enable");
  await client.send("Runtime.enable");

  for (const size of sizes) {
    await client.send("Emulation.setDeviceMetricsOverride", {
      width: size.width,
      height: size.height,
      deviceScaleFactor: 1,
      mobile: size.mobile,
    });
    for (const view of views) {
      const target = new URL(baseUrl);
      target.searchParams.set("view", view);
      target.searchParams.set("period", "7d");
      await client.send("Page.navigate", { url: target.toString() });
      await sleep(waitMs);
      const evaluation = await client.send("Runtime.evaluate", {
        returnByValue: true,
        expression: `(() => {
          const visible = (node) => {
            const style = getComputedStyle(node);
            const rect = node.getBoundingClientRect();
            return style.visibility !== "hidden" && style.display !== "none" && rect.width > 0 && rect.height > 0;
          };
          const unlabeledButtons = Array.from(document.querySelectorAll("button, a[href]"))
            .filter(visible)
            .filter((node) => !(node.textContent || "").trim() && !node.getAttribute("aria-label") && !node.getAttribute("title"))
            .length;
          const smallControls = Array.from(document.querySelectorAll("button, a[href], input:not([type=checkbox]):not([type=radio]), select"))
            .filter(visible)
            .filter((node) => {
              const rect = node.getBoundingClientRect();
              const parentRect = node.closest("label")?.getBoundingClientRect();
              const hasLargeLabelTarget = parentRect && parentRect.width >= 32 && parentRect.height >= 32;
              return !hasLargeLabelTarget && (rect.width < 32 || rect.height < 32);
            })
            .map((node) => {
              const rect = node.getBoundingClientRect();
              return {
                tag: node.tagName.toLowerCase(),
                cls: typeof node.className === "string" ? node.className : "",
                text: (node.textContent || node.getAttribute("aria-label") || "").trim().slice(0, 48),
                width: Math.round(rect.width),
                height: Math.round(rect.height),
              };
            });
          return {
            title: document.querySelector(".topbar-title")?.textContent?.trim() ?? "",
            documentWidth: document.documentElement.scrollWidth,
            viewportWidth: window.innerWidth,
            apiUnavailable: Array.from(document.querySelectorAll(".data-banner, .side-status"))
              .some((node) => /API недоступен|timeout|HTTP 4|HTTP 5/i.test(node.textContent || "")),
            mainBlocks: document.querySelectorAll("main section, main .panel, main .hero").length,
            unlabeledButtons,
            tooSmall: smallControls.length,
            smallControls,
          };
        })()`,
      });
      const result = evaluation.result.result.value;
      const row = { view, size: size.name, ...result };
      rows.push(row);
      if (!result.title) failures.push(`${view}/${size.name}: missing page title`);
      if (result.documentWidth > result.viewportWidth + 1) {
        failures.push(`${view}/${size.name}: horizontal overflow ${result.documentWidth}/${result.viewportWidth}`);
      }
      if (result.apiUnavailable) failures.push(`${view}/${size.name}: API unavailable banner`);
      if (result.mainBlocks === 0) failures.push(`${view}/${size.name}: no main content blocks`);
      if (result.unlabeledButtons > 0) failures.push(`${view}/${size.name}: ${result.unlabeledButtons} unlabeled controls`);
      if (size.mobile && result.tooSmall > 0) failures.push(`${view}/${size.name}: ${result.tooSmall} controls below 32px`);
    }
  }
  client.close();
} finally {
  try {
    browser.kill();
  } catch {
    // Browser may already have exited.
  }
  setTimeout(() => {
    try {
      rmSync(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
    } catch {
      // Windows can keep the profile locked briefly.
    }
  }, 300);
}

console.log(JSON.stringify({ checked: rows.length, failures, rows }, null, 2));
if (failures.length > 0) process.exitCode = 1;
