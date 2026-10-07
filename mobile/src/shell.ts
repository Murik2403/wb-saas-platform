// Runs inside the WebView. Uses window.Capacitor.Plugins (native bridge) so no bundler is needed.
// Loaded by www/index.html (offline screen) and meant to be served by the gateway to the
// remote site as well (see README "Remote shell script").
type Plug = Record<string, any>;
interface Cap { isNativePlatform(): boolean; getPlatform(): string; Plugins: Record<string, Plug | undefined> }

(() => {
  const cap = (window as unknown as { Capacitor?: Cap }).Capacitor;
  if (!cap || !cap.isNativePlatform()) return;
  const P = cap.Plugins;
  const ORIGIN = "https://marketshelper.ru";
  const isOwn = (h: string) => h === "marketshelper.ru" || h.endsWith(".marketshelper.ru");

  // Deep links: https://marketshelper.ru/<path> -> navigate inside the WebView.
  const DEEP = [/^\/verify-email(\/|$)/, /^\/reset-password(\/|$)/, /^\/team\/accept(\/|$)/];
  P.App?.addListener("appUrlOpen", (e: { url: string }) => {
    try {
      const u = new URL(e.url);
      if (u.protocol === "https:" && u.hostname === "marketshelper.ru" && DEEP.some((r) => r.test(u.pathname))) {
        window.location.href = ORIGIN + u.pathname + u.search + u.hash;
      }
    } catch { /* ignore malformed url */ }
  });

  // Android back button: history back, exit at the root.
  P.App?.addListener("backButton", (e: { canGoBack: boolean }) => {
    if (e.canGoBack || window.history.length > 1) window.history.back();
    else P.App?.exitApp();
  });

  // External links go to the system browser.
  const openExternal = (url: string) => P.Browser?.open({ url });
  const external = (href: string) => {
    try {
      const u = new URL(href, window.location.href);
      if (u.protocol === "http:" || u.protocol === "https:") return isOwn(u.hostname) ? null : u.href;
      return null;
    } catch { return null; }
  };
  document.addEventListener("click", (ev) => {
    const a = (ev.target as Element | null)?.closest?.("a[href]") as HTMLAnchorElement | null;
    const ext = a && external(a.href);
    if (ext) { ev.preventDefault(); openExternal(ext); }
  }, true);
  const nativeOpen = window.open.bind(window);
  window.open = ((url?: string | URL, ...rest: any[]) => {
    const ext = url ? external(String(url)) : null;
    if (ext) { openExternal(ext); return null; }
    return nativeOpen(url as string, ...rest);
  }) as typeof window.open;

  // Safe area + status bar follow the system theme.
  const root = document.documentElement;
  root.classList.add("is-native-app", "platform-" + cap.getPlatform());
  const dark = window.matchMedia("(prefers-color-scheme: dark)");
  const bar = () => P.StatusBar?.setStyle({ style: dark.matches ? "DARK" : "LIGHT" });
  bar();
  dark.addEventListener("change", bar);

  // Splash: hide once the page is up (launchAutoHide also covers it).
  window.addEventListener("load", () => P.SplashScreen?.hide());
})();
