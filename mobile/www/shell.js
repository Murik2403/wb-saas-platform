"use strict";
(() => {
    var _a, _b;
    const cap = window.Capacitor;
    if (!cap || !cap.isNativePlatform())
        return;
    const P = cap.Plugins;
    const ORIGIN = "https://marketshelper.ru";
    const isOwn = (h) => h === "marketshelper.ru" || h.endsWith(".marketshelper.ru");
    // Deep links: https://marketshelper.ru/<path> -> navigate inside the WebView.
    const DEEP = [/^\/verify-email(\/|$)/, /^\/reset-password(\/|$)/, /^\/team\/accept(\/|$)/];
    (_a = P.App) === null || _a === void 0 ? void 0 : _a.addListener("appUrlOpen", (e) => {
        try {
            const u = new URL(e.url);
            if (u.protocol === "https:" && u.hostname === "marketshelper.ru" && DEEP.some((r) => r.test(u.pathname))) {
                window.location.href = ORIGIN + u.pathname + u.search + u.hash;
            }
        }
        catch { /* ignore malformed url */ }
    });
    // Android back button: history back, exit at the root.
    (_b = P.App) === null || _b === void 0 ? void 0 : _b.addListener("backButton", (e) => {
        var _a;
        if (e.canGoBack || window.history.length > 1)
            window.history.back();
        else
            (_a = P.App) === null || _a === void 0 ? void 0 : _a.exitApp();
    });
    // External links go to the system browser.
    const openExternal = (url) => { var _a; return (_a = P.Browser) === null || _a === void 0 ? void 0 : _a.open({ url }); };
    const external = (href) => {
        try {
            const u = new URL(href, window.location.href);
            if (u.protocol === "http:" || u.protocol === "https:")
                return isOwn(u.hostname) ? null : u.href;
            return null;
        }
        catch {
            return null;
        }
    };
    document.addEventListener("click", (ev) => {
        var _a, _b;
        const a = (_b = (_a = ev.target) === null || _a === void 0 ? void 0 : _a.closest) === null || _b === void 0 ? void 0 : _b.call(_a, "a[href]");
        const ext = a && external(a.href);
        if (ext) {
            ev.preventDefault();
            openExternal(ext);
        }
    }, true);
    const nativeOpen = window.open.bind(window);
    window.open = ((url, ...rest) => {
        const ext = url ? external(String(url)) : null;
        if (ext) {
            openExternal(ext);
            return null;
        }
        return nativeOpen(url, ...rest);
    });
    // Safe area + status bar follow the system theme.
    const root = document.documentElement;
    root.classList.add("is-native-app", "platform-" + cap.getPlatform());
    const dark = window.matchMedia("(prefers-color-scheme: dark)");
    const bar = () => { var _a; return (_a = P.StatusBar) === null || _a === void 0 ? void 0 : _a.setStyle({ style: dark.matches ? "DARK" : "LIGHT" }); };
    bar();
    dark.addEventListener("change", bar);
    // Splash: hide once the page is up (launchAutoHide also covers it).
    window.addEventListener("load", () => { var _a; return (_a = P.SplashScreen) === null || _a === void 0 ? void 0 : _a.hide(); });
})();
