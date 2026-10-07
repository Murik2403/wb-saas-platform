import "./pwa-updates.css";

/** No automatic reload: other tabs and in-progress forms retain their state. */
export function startPwaUpdates(reload: () => void = () => window.location.reload()): () => void {
  let disposed = false;
  let optedIn = false;
  let banner: HTMLElement | null = null;
  let baseline: string | null = null;
  let dismissed = "";
  let checking = false;
  let controller: AbortController | null = null;
  const cleanups: (() => void)[] = [];
  function offer(key: string, apply: () => void) {
    if (disposed || banner || key === dismissed) return;
    banner = document.createElement("aside");
    banner.className = "pwa-update-banner";
    banner.setAttribute("role", "status");
    const text = document.createElement("p");
    text.textContent = "Доступно обновление. Сначала сохраните введённые данные и завершите операции: обновление перезагрузит страницу.";
    const update = document.createElement("button");
    update.type = "button"; update.textContent = "Обновить страницу";
    update.onclick = () => { optedIn = true; update.disabled = true; apply(); };
    const later = document.createElement("button");
    later.type = "button"; later.textContent = "Позже";
    later.onclick = () => { dismissed = key; banner?.remove(); banner = null; };
    banner.append(text, update, later); document.body.append(banner);
  }
  async function checkBuild() {
    if (disposed || checking || document.visibilityState !== "visible") return;
    checking = true; controller = new AbortController();
    const timeout = window.setTimeout(() => controller?.abort(), 5000);
    try {
      const response = await fetch("/build-id.txt", { cache: "no-store", credentials: "same-origin", signal: controller.signal });
      if (!response.ok) return;
      const version = (await response.text()).trim();
      if (!/^[a-f0-9]{7,64}$/i.test(version) || disposed) return;
      if (baseline === null) baseline = version;
      else if (version !== baseline) offer(version, reload);
    } catch { /* Offline or unauthenticated probes do not create update claims. */ }
    finally { window.clearTimeout(timeout); checking = false; }
  }
  function changed() { if (optedIn && !disposed) reload(); }
  async function register() {
    if (!("serviceWorker" in navigator) || disposed) return;
    try {
      const registration = await navigator.serviceWorker.register("/sw.js");
      if (disposed) return;
      const waiting = () => {
        if (registration.waiting && navigator.serviceWorker.controller)
          offer("service-worker", () => registration.waiting?.postMessage({ type: "MH_ACTIVATE_UPDATE" }));
      };
      waiting();
      const found = () => {
        const worker = registration.installing;
        if (!worker) return;
        const state = () => { if (worker.state === "installed") waiting(); };
        worker.addEventListener("statechange", state);
        cleanups.push(() => worker.removeEventListener("statechange", state));
      };
      registration.addEventListener("updatefound", found);
      cleanups.push(() => registration.removeEventListener("updatefound", found));
    } catch { /* Unsupported/private browsing must not block the cabinet. */ }
  }
  navigator.serviceWorker?.addEventListener("controllerchange", changed);
  cleanups.push(() => navigator.serviceWorker?.removeEventListener("controllerchange", changed));
  document.addEventListener("visibilitychange", checkBuild);
  cleanups.push(() => document.removeEventListener("visibilitychange", checkBuild));
  const timer = window.setInterval(() => { void checkBuild(); }, 5 * 60 * 1000);
  void checkBuild(); void register();
  return () => { disposed = true; controller?.abort(); window.clearInterval(timer); banner?.remove(); cleanups.forEach((cleanup) => cleanup()); };
}
