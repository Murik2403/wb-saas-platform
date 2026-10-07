import { afterEach, describe, expect, it, vi } from "vitest";
import { installStoreModeGuards, isBillingPath, isNativeApp, platform } from "./app-shell";

const ua = (value: string) => vi.spyOn(navigator, "userAgent", "get").mockReturnValue(value);
afterEach(() => { vi.restoreAllMocks(); delete (window as any).Capacitor; });

describe("app-shell", () => {
  it("is web by default", () => { expect(isNativeApp()).toBe(false); expect(platform()).toBe("web"); });
  it("detects the app by user agent", () => {
    ua("Mozilla/5.0 (iPhone) MarketsHelperApp/1.0");
    expect(isNativeApp()).toBe(true); expect(platform()).toBe("ios");
  });
  it("detects Capacitor", () => {
    (window as any).Capacitor = { isNativePlatform: () => true, getPlatform: () => "android" };
    expect(isNativeApp()).toBe(true); expect(platform()).toBe("android");
  });
  it("ignores a non-native Capacitor", () => {
    (window as any).Capacitor = { isNativePlatform: () => false };
    expect(isNativeApp()).toBe(false);
  });
  it("recognises billing paths", () => {
    expect(isBillingPath("https://marketshelper.ru/billing")).toBe(true);
    expect(isBillingPath("/pricing/x")).toBe(true);
    expect(isBillingPath("/account")).toBe(false);
  });
  it("blocks billing clicks and window.open only in the app", () => {
    ua("MarketsHelperApp/1.0");
    const realOpen = vi.fn(() => ({}) as Window); window.open = realOpen as any;
    installStoreModeGuards();
    expect(window.open("/billing")).toBeNull(); expect(realOpen).not.toHaveBeenCalled();
    window.open("/account"); expect(realOpen).toHaveBeenCalled();
    const a = document.createElement("a"); a.href = "/pricing"; document.body.append(a);
    const ev = new MouseEvent("click", { bubbles: true, cancelable: true }); a.dispatchEvent(ev);
    expect(ev.defaultPrevented).toBe(true); a.remove();
  });
});
