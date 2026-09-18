/* Optional public-page analytics. Auth/payment screens never receive a counter. */
(() => {
  const root = document.getElementById("privacy-settings");
  if (!root) return;
  const notice = document.getElementById("cookie-notice");
  const status = document.getElementById("cookie-status");
  const counter = root.dataset.counter || "";
  const available = root.dataset.available === "true";
  let choice = root.dataset.choice || "";
  let initialized = false;
  let saving = false;
  const storageKey = "marketshelper_cookie_notice_v2";

  function startCounter() {
    if (choice !== "analytics" || !counter || initialized) return;
    initialized = true;
    window.ym = window.ym || function () { (window.ym.a = window.ym.a || []).push(arguments); };
    window.ym.l = Date.now();
    const script = document.createElement("script");
    script.async = true;
    script.src = "https://mc.yandex.ru/metrika/tag.js";
    document.head.appendChild(script);
    // Exclude arbitrary query parameters and fragments (which may contain PII).
    const safeUrl = new URL(window.location.pathname, window.location.origin);
    const current = new URL(window.location.href);
    for (const key of ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]) {
      const value = (current.searchParams.get(key) || "").trim().toLowerCase();
      if (/^[a-z0-9][a-z0-9_-]{0,63}$/.test(value) && !/(?:sk[-_]|bearer|token[-_]|secret[-_]|eyj)|[a-f0-9]{24,}/i.test(value)) safeUrl.searchParams.set(key, value);
    }
    window.ym(Number(counter), "init", { defer: true, webvisor: false, clickmap: false, trackLinks: false });
    window.ym(Number(counter), "hit", safeUrl.toString(), { referer: "" });
  }

  function remember(value) {
    try { localStorage.setItem(storageKey, JSON.stringify({ choice: value, version: 2 })); } catch (_) { /* Storage may be unavailable. */ }
  }

  async function choose(value) {
    if (saving) return;
    status.textContent = "";
    if (!available) {
      remember("essential");
      choice = "essential";
      notice.hidden = true;
      return;
    }
    const payload = { choice: value };
    if (value === "analytics") {
      const query = new URLSearchParams(window.location.search);
      for (const key of ["utm_source", "utm_medium", "utm_campaign", "utm_content", "utm_term"]) payload[key] = query.get(key) || "";
    }
    saving = true;
    try {
      const response = await fetch("/privacy/analytics-consent", {
        method: "POST", headers: { "Content-Type": "application/json" },
        credentials: "same-origin", body: JSON.stringify(payload),
      });
      if (!response.ok) throw new Error("consent failed");
      choice = value;
      remember(choice);
      notice.hidden = true;
      if (choice === "essential" && initialized) {
        if (window.ym && counter) window.ym(Number(counter), "destruct");
        // Reload removes the already-loaded third-party script completely.
        window.location.reload();
      } else startCounter();
    } catch (_) {
      status.textContent = "Не удалось сохранить выбор. Предыдущая настройка остаётся в силе; попробуйте ещё раз.";
    } finally {
      saving = false;
    }
  }

  document.getElementById("cookie-accept").addEventListener("click", () => choose("analytics"));
  document.getElementById("cookie-decline").addEventListener("click", () => choose("essential"));
  document.getElementById("cookie-settings-open").addEventListener("click", () => { notice.hidden = false; document.getElementById("cookie-decline").focus(); });
  if (available) {
    notice.hidden = Boolean(choice);
  } else {
    try { notice.hidden = Boolean(localStorage.getItem(storageKey)); } catch (_) { /* Keep notice visible. */ }
  }
  startCounter();
})();
