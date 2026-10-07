// Standalone app-shell hooks: no dependencies, no build step (plain ES2017).
// Canonical source. Copies (keep identical, test enforces it):
//   gateway/static/app-shell.js, mh-redesign-lab/public/app-shell.js
// Runs inside the Capacitor WebView on remote pages (and on the offline screen) and
// uses only window.Capacitor / window.Capacitor.Plugins. Does nothing outside the app.
(function () {
  "use strict";
  var cap = window.Capacitor;
  var inApp = /MarketsHelperApp\//.test(navigator.userAgent) ||
    !!(cap && typeof cap.isNativePlatform === "function" && cap.isNativePlatform());
  if (!inApp || !cap || !cap.Plugins || window.__mhAppShell) return;
  window.__mhAppShell = true;

  var P = cap.Plugins;
  var ORIGIN = "https://marketshelper.ru";
  var isOwn = function (h) { return h === "marketshelper.ru" || h.slice(-".marketshelper.ru".length) === ".marketshelper.ru"; };

  // Deep links: https://marketshelper.ru/<path> -> navigate inside the WebView.
  var DEEP = [/^\/verify-email(\/|$)/, /^\/reset-password(\/|$)/, /^\/team\/accept(\/|$)/];
  if (P.App) {
    P.App.addListener("appUrlOpen", function (e) {
      try {
        var u = new URL(e.url);
        if (u.protocol === "https:" && u.hostname === "marketshelper.ru" && DEEP.some(function (r) { return r.test(u.pathname); })) {
          window.location.href = ORIGIN + u.pathname + u.search + u.hash;
        }
      } catch (err) { /* ignore malformed url */ }
    });
    // Android back button: history back, exit at the root.
    P.App.addListener("backButton", function (e) {
      if (e.canGoBack || window.history.length > 1) window.history.back();
      else P.App.exitApp();
    });
  }

  // External links go to the system browser.
  var openExternal = function (url) { if (P.Browser) P.Browser.open({ url: url }); };
  var external = function (href) {
    try {
      var u = new URL(href, window.location.href);
      if (u.protocol === "http:" || u.protocol === "https:") return isOwn(u.hostname) ? null : u.href;
    } catch (err) { /* not a url */ }
    return null;
  };
  document.addEventListener("click", function (ev) {
    var t = ev.target;
    var a = t && t.closest ? t.closest("a[href]") : null;
    var ext = a && external(a.href);
    if (ext) { ev.preventDefault(); openExternal(ext); }
  }, true);
  var nativeOpen = window.open.bind(window);
  window.open = function (url) {
    var ext = url ? external(String(url)) : null;
    if (ext) { openExternal(ext); return null; }
    return nativeOpen.apply(window, arguments);
  };

  // Status bar follows the system theme.
  var root = document.documentElement;
  root.classList.add("is-native-app", "platform-" + (cap.getPlatform ? cap.getPlatform() : "native"));
  var dark = window.matchMedia("(prefers-color-scheme: dark)");
  var bar = function () { if (P.StatusBar) P.StatusBar.setStyle({ style: dark.matches ? "DARK" : "LIGHT" }); };
  bar();
  if (dark.addEventListener) dark.addEventListener("change", bar);

  // Splash: hide once the page is up (launchAutoHide also covers it).
  var hide = function () { if (P.SplashScreen) P.SplashScreen.hide(); };
  if (document.readyState === "complete") hide(); else window.addEventListener("load", hide);
})();
