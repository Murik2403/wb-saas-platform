import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ThemeToggle } from "./ThemeToggle";

afterEach(() => { cleanup(); vi.restoreAllMocks(); localStorage.clear(); delete document.documentElement.dataset.theme; });
it("switches both ways and restores the visitor choice after remount", () => {
  const first = render(<ThemeToggle />);
  fireEvent.click(screen.getByRole("button", { name: "Включить светлую тему" }));
  expect(document.documentElement.dataset.theme).toBe("light");
  first.unmount();
  render(<ThemeToggle />);
  expect(screen.getByRole("button", { name: "Включить тёмную тему" }).getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(screen.getByRole("button", { name: "Включить тёмную тему" }));
  expect(localStorage.getItem("marketshelper-theme")).toBe("dark");
});
it("continues to work when browser storage is blocked", () => {
  vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
  render(<ThemeToggle />);
  fireEvent.click(screen.getByRole("button", { name: "Включить светлую тему" }));
  expect(document.documentElement.dataset.theme).toBe("light");
  expect(screen.getByRole("button", { name: "Включить тёмную тему" })).toBeTruthy();
});
