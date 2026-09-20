const GOALS = new Set(["account_created", "trial_started", "first_useful_report"]);
let trackerPromise: Promise<number | null> | null = null;

function publicOrigin(): string {
  if (window.location.hostname.endsWith(".app.marketshelper.ru") || window.location.hostname === "marketshelper.ru") {
    return "https://marketshelper.ru";
  }
  return window.location.origin;
}

async function loadTracker(): Promise<number | null> {
  if (trackerPromise) return trackerPromise;
  trackerPromise = fetch(`${publicOrigin()}/privacy/analytics-config`, { credentials: "include" })
    .then(async (response) => {
      if (!response.ok) return null;
      const config = await response.json() as { enabled?: boolean; counter?: string };
      const counter = Number(config.counter);
      if (!config.enabled || !Number.isInteger(counter) || counter <= 0) return null;
      window.ym = window.ym || function (...args: unknown[]) { (window.ym!.a = window.ym!.a || []).push(args); };
      window.ym.l = Date.now();
      const script = document.createElement("script");
      script.async = true;
      script.src = "https://mc.yandex.ru/metrika/tag.js";
      document.head.appendChild(script);
      window.ym(counter, "init", { defer: true, webvisor: false, clickmap: false, trackLinks: false });
      return counter;
    })
    .catch(() => null);
  return trackerPromise;
}

export async function trackMetrikaGoal(goal: string): Promise<boolean> {
  if (!GOALS.has(goal)) return false;
  const storageKey = `mh_metrika_goal_${goal}`;
  try {
    if (window.sessionStorage.getItem(storageKey) === "1") return false;
  } catch (_) { /* Storage can be unavailable; consent remains server-gated. */ }
  const counter = await loadTracker();
  if (!counter || !window.ym) return false;
  window.ym(counter, "reachGoal", goal);
  try { window.sessionStorage.setItem(storageKey, "1"); } catch (_) { /* best effort dedupe */ }
  return true;
}

declare global {
  interface Window {
    ym?: ((...args: unknown[]) => void) & { a?: unknown[]; l?: number };
  }
}

