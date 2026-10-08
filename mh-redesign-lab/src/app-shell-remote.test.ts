// @vitest-environment jsdom
import source from "../public/app-shell.js?raw";
import { beforeEach, describe, expect, it, vi } from "vitest";


function run(native: boolean) {
  const listeners: Record<string, (e: unknown) => void> = {};
  const Plugins = {
    App: { addListener: vi.fn((n: string, cb: (e: unknown) => void) => { listeners[n] = cb; }), exitApp: vi.fn() },
    Browser: { open: vi.fn() },
    StatusBar: { setStyle: vi.fn() },
    SplashScreen: { hide: vi.fn() },
  };
  (window as any).__mhAppShell = undefined;
  (window as any).Capacitor = native ? { isNativePlatform: () => true, getPlatform: () => "android", Plugins } : undefined;
  window.matchMedia = (() => ({ matches: false, addEventListener: () => {} })) as any;
  (0, eval)(source);
  return { Plugins, listeners };
}

describe("app-shell.js", () => {
  beforeEach(() => { document.body.innerHTML = ""; document.documentElement.className = ""; });

  it("does nothing outside the app", () => {
    const add = vi.spyOn(document, "addEventListener");
    const { Plugins } = run(false);
    expect(add).not.toHaveBeenCalled();
    expect(Plugins.App.addListener).not.toHaveBeenCalled();
    expect(document.documentElement.classList.contains("is-native-app")).toBe(false);
    add.mockRestore();
  });

  it("opens external links in the system browser, keeps own links", () => {
    const { Plugins } = run(true);
    document.body.innerHTML = '<a id="e" href="https://example.com/x">e</a><a id="i" href="https://app.marketshelper.ru/y">i</a>';
    const click = (id: string) => {
      const ev = new MouseEvent("click", { bubbles: true, cancelable: true });
      document.getElementById(id)!.dispatchEvent(ev);
      return ev.defaultPrevented;
    };
    expect(click("e")).toBe(true);
    expect(Plugins.Browser.open).toHaveBeenCalledWith({ url: "https://example.com/x" });
    expect(click("i")).toBe(false);
    expect(Plugins.Browser.open).toHaveBeenCalledTimes(1);
    expect(Plugins.StatusBar.setStyle).toHaveBeenCalledWith({ style: "LIGHT" });
    expect(document.documentElement.classList.contains("platform-android")).toBe(true);
  });

  it("back button uses history; deep links navigate to marketshelper.ru, others ignored", () => {
    const listeners: Record<string, (e: unknown) => void> = {};
    const App = { addListener: (n: string, cb: (e: unknown) => void) => { listeners[n] = cb; }, exitApp: vi.fn() };
    const fake: any = {
      Capacitor: { isNativePlatform: () => true, getPlatform: () => "ios", Plugins: { App } },
      location: { href: "https://marketshelper.ru/login" },
      history: { length: 1, back: vi.fn() },
      matchMedia: () => ({ matches: true, addEventListener: () => {} }),
      open: () => null,
      addEventListener: () => {},
    };
    new Function("window", source)(fake);
    listeners.appUrlOpen({ url: "https://marketshelper.ru/reset-password?token=abc#x" });
    expect(fake.location.href).toBe("https://marketshelper.ru/reset-password?token=abc#x");
    for (const url of ["https://marketshelper.ru/admin", "http://marketshelper.ru/verify-email", "https://evil.test/verify-email", "nonsense"]) {
      fake.location.href = "https://marketshelper.ru/login";
      listeners.appUrlOpen({ url });
      expect(fake.location.href).toBe("https://marketshelper.ru/login");
    }
    listeners.backButton({ canGoBack: true });
    expect(fake.history.back).toHaveBeenCalled();
    listeners.backButton({ canGoBack: false });
    expect(App.exitApp).toHaveBeenCalled();
  });
});
