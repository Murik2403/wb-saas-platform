// Detects the Capacitor wrapper of the mobile apps (UA contains `MarketsHelperApp/`, `window.Capacitor` is injected).
type CapacitorLike = {
  isNativePlatform?: () => boolean;
  getPlatform?: () => string;
  Plugins?: Record<string, any>;
};

export function capacitor(): CapacitorLike | undefined {
  return typeof window === "undefined" ? undefined : (window as unknown as { Capacitor?: CapacitorLike }).Capacitor;
}

export function isNativeApp(): boolean {
  if (typeof navigator !== "undefined" && /MarketsHelperApp\//.test(navigator.userAgent)) return true;
  try {
    return capacitor()?.isNativePlatform?.() === true;
  } catch {
    return false;
  }
}

export function platform(): "ios" | "android" | "web" {
  if (!isNativeApp()) return "web";
  let p = "";
  try {
    p = capacitor()?.getPlatform?.() ?? "";
  } catch {
    /* fall through to UA */
  }
  if (p !== "ios" && p !== "android") {
    const ua = typeof navigator !== "undefined" ? navigator.userAgent : "";
    p = /iPhone|iPad|iPod/.test(ua) ? "ios" : /Android/.test(ua) ? "android" : "";
  }
  return p === "ios" || p === "android" ? p : "web";
}

/** Store builds must not sell or link to subscriptions (App Store / Play policy). */
export function isBillingPath(url: string): boolean {
  try {
    return /^\/(billing|pricing)(\/|$)/.test(new URL(url, window.location.href).pathname);
  } catch {
    return false;
  }
}

/** In the store build, subscription pages must not be reachable from the cabinet at all. */
export function installStoreModeGuards(): void {
  if (!isNativeApp()) return;
  document.addEventListener("click", (event) => {
    const anchor = (event.target as Element | null)?.closest?.("a[href]");
    if (anchor && isBillingPath(anchor.getAttribute("href") ?? "")) { event.preventDefault(); event.stopPropagation(); }
  }, true);
  const open = window.open.bind(window);
  window.open = ((url?: string | URL, ...rest: unknown[]) =>
    url && isBillingPath(String(url)) ? null : open(url as string, ...(rest as [string?, string?]))) as typeof window.open;
}
