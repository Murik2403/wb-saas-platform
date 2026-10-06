import { useEffect, useState, type ReactNode } from "react";
import { API_BASE_URL } from "./data-source";
import { clearMarketplaceContext, selectMarketplace, validateMarketplaceSession, type MarketplaceSession } from "./marketplace-context";
import "./MarketplaceBoundary.css";

type Marketplace = "wb" | "ozon";
const preferenceKey = (mp: Marketplace) => `mh-marketplace-view:${mp}`;
const preferenceFields = ["view", "period", "start", "end", "shop"];
function initialMarketplace(): string {
  const specified = new URL(window.location.href).searchParams.get("marketplace");
  if (specified !== null) return specified;
  try { const saved = localStorage.getItem("mh-marketplace"); return saved === "ozon" ? "ozon" : "wb"; } catch { return "wb"; }
}
export function marketplaceSwitchUrl(next: Marketplace): string {
  const url = new URL(window.location.href);
  const previous = url.searchParams.get("marketplace") === "ozon" ? "ozon" : "wb";
  try {
    localStorage.setItem(preferenceKey(previous), JSON.stringify(Object.fromEntries(preferenceFields.map(key => [key, url.searchParams.get(key)]))));
    const saved = JSON.parse(localStorage.getItem(preferenceKey(next)) ?? "null");
    if (saved && typeof saved === "object") for (const key of preferenceFields) {
      const value = saved[key]; if (typeof value === "string" && value.length <= 40) url.searchParams.set(key, value); else url.searchParams.delete(key);
    }
    localStorage.setItem("mh-marketplace", next);
  } catch { /* URL selection and server authorization remain sufficient. */ }
  url.searchParams.set("marketplace", next);
  if (next === "ozon") url.searchParams.delete("shop");
  url.hash = "";
  return url.toString();
}

export function MarketplaceBoundary({ wb, ozon }: { wb: ReactNode; ozon: (session: MarketplaceSession) => ReactNode }) {
  const [selected, setSelected] = useState(initialMarketplace);
  const [session, setSession] = useState<MarketplaceSession | null>(null);
  const [error, setError] = useState("");
  const [retry, setRetry] = useState(0);
  useEffect(() => {
    clearMarketplaceContext(); setSession(null); setError("");
    if (selected !== "wb" && selected !== "ozon") { setError("Неизвестная площадка. Выберите WB или Ozon."); return; }
    const controller = new AbortController();
    globalThis.fetch(`${API_BASE_URL}/api/v1/marketplace-context/session?marketplace=${selected}`, { headers: { "X-MH-Marketplace": selected }, credentials: "same-origin", cache: "no-store", signal: controller.signal })
      .then(async response => { const body = await response.json(); if (!response.ok) throw new Error(typeof body.detail === "string" ? body.detail : body.detail?.message ?? "Доступ к площадке не подтверждён"); return validateMarketplaceSession(body); })
      .then(next => { if (controller.signal.aborted) return; if (next.marketplace !== selected || !next.authorized) throw new Error("Площадка ответа не совпадает с выбранной"); selectMarketplace(next); setSession(next); })
      .catch(caught => { if (!controller.signal.aborted) { clearMarketplaceContext(); setError(caught instanceof Error ? caught.message : "Не удалось подтвердить площадку"); } });
    return () => { controller.abort(); clearMarketplaceContext(); };
  }, [selected, retry]);
  useEffect(() => {
    const changed = () => { setSession(null); clearMarketplaceContext(); setSelected(new URL(window.location.href).searchParams.get("marketplace") ?? "wb"); setRetry(v => v + 1); };
    window.addEventListener("popstate", changed); return () => window.removeEventListener("popstate", changed);
  }, []);
  const change = (next: Marketplace) => {
    if (next === selected) return;
    clearMarketplaceContext(); setSession(null); setError("");
    history.replaceState(null, "", marketplaceSwitchUrl(next)); setSelected(next);
  };
  return <>
    <section className="marketplace-bar" aria-label="Выбор маркетплейса">
      <span>Площадка</span><div role="group" aria-label="Маркетплейс"><button type="button" aria-pressed={selected === "wb"} onClick={() => change("wb")}>Wildberries</button><button type="button" aria-pressed={selected === "ozon"} onClick={() => change("ozon")}>Ozon</button></div>
      <p>{session ? `${selected === "ozon" ? "Ozon" : "WB"} · ${session.is_demo ? "демонстрационный кабинет" : session.can_write ? "действия в пределах ваших прав" : "только просмотр"}` : "Проверяем доступ к площадке"}</p>
    </section>
    {error ? <section className="panel marketplace-error" role="alert"><h1>Площадка недоступна</h1><p>{error}</p><button className="secondary-button" type="button" onClick={() => setRetry(v => v + 1)}>Повторить проверку</button></section> : !session ? <section className="panel" role="status">Подтверждаем доступ к {selected === "ozon" ? "Ozon" : "WB"}…</section> : <div key={session.marketplace}>{session.marketplace === "wb" ? wb : ozon(session)}</div>}
  </>;
}
