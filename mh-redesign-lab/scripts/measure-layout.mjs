import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const edgePath =
  process.env.EDGE_PATH ?? "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const url = process.argv[2] ?? "http://127.0.0.1:5177/";
const [width = "390", height = "844"] = (process.argv[3] ?? "390x844").split("x");
const waitMs = Number(process.argv[4] ?? 1200);
const port = 9400 + Math.floor(Math.random() * 300);
const profile = mkdtempSync(join(tmpdir(), "mh-layout-"));

const browser = spawn(
  edgePath,
  [
    "--headless=new",
    "--disable-gpu",
    "--hide-scrollbars",
    "--force-device-scale-factor=1",
    `--remote-debugging-port=${port}`,
    `--user-data-dir=${profile}`,
    `--window-size=${width},${height}`,
    url,
  ],
  { stdio: "ignore" },
);

const cleanup = () => {
  try {
    browser.kill();
  } catch {
    // Browser may already be closed.
  }
  setTimeout(() => {
    try {
      rmSync(profile, { recursive: true, force: true, maxRetries: 3, retryDelay: 100 });
    } catch {
      // Windows can keep profile locks briefly after Edge exits.
    }
  }, 300);
};

async function getJson(path) {
  const endpoint = `http://127.0.0.1:${port}${path}`;
  for (let attempt = 0; attempt < 40; attempt += 1) {
    try {
      const response = await fetch(endpoint);
      if (response.ok) {
        return response.json();
      }
    } catch {
      await new Promise((resolve) => setTimeout(resolve, 150));
    }
  }
  throw new Error(`Unable to reach ${endpoint}`);
}

function cdp(wsUrl) {
  const ws = new WebSocket(wsUrl);
  let nextId = 1;
  const pending = new Map();

  ws.addEventListener("message", (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      pending.get(message.id)(message);
      pending.delete(message.id);
    }
  });

  return new Promise((resolve) => {
    ws.addEventListener("open", () => {
      resolve({
        send(method, params = {}) {
          const id = nextId;
          nextId += 1;
          ws.send(JSON.stringify({ id, method, params }));
          return new Promise((done) => pending.set(id, done));
        },
        close() {
          ws.close();
        },
      });
    });
  });
}

try {
  const tabs = await getJson("/json");
  const page = tabs.find((tab) => tab.type === "page");
  const client = await cdp(page.webSocketDebuggerUrl);
  await client.send("Page.enable");
  await client.send("Runtime.enable");
  await client.send("Emulation.setDeviceMetricsOverride", {
    width: Number(width),
    height: Number(height),
    deviceScaleFactor: 1,
    mobile: Number(width) <= 680,
  });
  await client.send("Page.navigate", { url });
  await client.send("Runtime.evaluate", {
    awaitPromise: true,
    expression: `new Promise((resolve) => {
      const started = Date.now();
      const tick = () => {
        if (document.body && document.readyState !== "loading" && Date.now() - started >= ${waitMs}) {
          resolve(true);
          return;
        }
        setTimeout(tick, 100);
      };
      tick();
    })`,
  });
  const result = await client.send("Runtime.evaluate", {
    returnByValue: true,
    expression: `(() => {
      const offenders = Array.from(document.querySelectorAll("*"))
        .map((node) => {
          const rect = node.getBoundingClientRect();
          const style = getComputedStyle(node);
          const intentionalEllipsis =
            style.overflowX === "hidden" &&
            style.textOverflow === "ellipsis" &&
            style.whiteSpace === "nowrap";
          return {
            tag: node.tagName.toLowerCase(),
            cls: typeof node.className === "string" ? node.className : "",
            text: (node.textContent || "").trim().replace(/\\s+/g, " ").slice(0, 64),
            left: Math.round(rect.left),
            right: Math.round(rect.right),
            width: Math.round(rect.width),
            scrollWidth: node.scrollWidth,
            clientWidth: node.clientWidth,
            intentionalEllipsis
          };
        })
        .filter((node) =>
          !node.intentionalEllipsis &&
          (node.right > window.innerWidth + 1 || node.scrollWidth > node.clientWidth + 1)
        )
        .slice(0, 40);
      return {
        innerWidth: window.innerWidth,
        devicePixelRatio: window.devicePixelRatio,
        documentScrollWidth: document.documentElement.scrollWidth,
        bodyScrollWidth: document.body?.scrollWidth ?? 0,
        sideStatus: document.querySelector(".side-status")?.textContent?.trim().replace(/\\s+/g, " ") ?? null,
        dataBanner: document.querySelector(".data-banner")?.textContent?.trim().replace(/\\s+/g, " ") ?? null,
        metricValues: Array.from(document.querySelectorAll(".metric-card"))
          .slice(0, 4)
          .map((node) => node.textContent.trim().replace(/\\s+/g, " ")),
        offenders
      };
    })()`,
  });
  if (result.exceptionDetails || result.result.exceptionDetails) {
    console.error(JSON.stringify(result.exceptionDetails ?? result.result.exceptionDetails, null, 2));
    process.exitCode = 1;
  } else {
    console.log(JSON.stringify(result.result.result.value ?? result, null, 2));
  }
  client.close();
} finally {
  cleanup();
}
