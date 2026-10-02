import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { WorkspaceTabs } from "./WorkspaceTabs";

const tabs = [{ id: "a", label: "Первая" }, { id: "b", label: "Вторая" }, { id: "c", label: "Третья" }];
const panels = { a: <p>Содержимое A</p>, b: <p>Содержимое B</p>, c: <p>Содержимое C</p> };

beforeEach(() => window.localStorage.clear());
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("WorkspaceTabs", () => {
  it("shows the first tab, switches on click and mounts only the selected panel", () => {
    render(<WorkspaceTabs tabs={tabs} panels={panels} ariaLabel="Разделы" />);
    expect(screen.getByText("Содержимое A")).toBeTruthy();
    expect(screen.queryByText("Содержимое B")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Вторая" }));
    expect(screen.getByText("Содержимое B")).toBeTruthy();
    expect(screen.queryByText("Содержимое A")).toBeNull();
    expect(screen.getByRole("button", { name: "Вторая" }).getAttribute("aria-pressed")).toBe("true");
    expect(screen.getByRole("button", { name: "Первая" }).getAttribute("aria-pressed")).toBe("false");
  });

  it("remembers the last tab, ignores an unknown stored value, and survives blocked storage", () => {
    const first = render(<WorkspaceTabs tabs={tabs} panels={panels} ariaLabel="Разделы" storageKey="t.tab" />);
    fireEvent.click(screen.getByRole("button", { name: "Третья" }));
    first.unmount();
    render(<WorkspaceTabs tabs={tabs} panels={panels} ariaLabel="Разделы" storageKey="t.tab" />);
    expect(screen.getByText("Содержимое C")).toBeTruthy();
    cleanup();
    window.localStorage.setItem("t.tab", "removed-tab");
    render(<WorkspaceTabs tabs={tabs} panels={panels} ariaLabel="Разделы" storageKey="t.tab" />);
    expect(screen.getByText("Содержимое A")).toBeTruthy();
    cleanup();
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    render(<WorkspaceTabs tabs={tabs} panels={panels} ariaLabel="Разделы" storageKey="t.tab" />);
    fireEvent.click(screen.getByRole("button", { name: "Вторая" }));
    expect(screen.getByText("Содержимое B")).toBeTruthy();
  });

  it("opens the tab that owns a deep-link target, now and when the target changes", () => {
    const map = { "ads-campaigns": "a", "ads-analytics": "c" };
    const { rerender } = render(<WorkspaceTabs tabs={tabs} panels={panels} ariaLabel="Разделы" target="ads-analytics" targetMap={map} />);
    expect(screen.getByText("Содержимое C")).toBeTruthy();
    rerender(<WorkspaceTabs tabs={tabs} panels={panels} ariaLabel="Разделы" target="ads-campaigns" targetMap={map} />);
    expect(screen.getByText("Содержимое A")).toBeTruthy();
    rerender(<WorkspaceTabs tabs={tabs} panels={panels} ariaLabel="Разделы" target="unknown-target" targetMap={map} />);
    expect(screen.getByText("Содержимое A")).toBeTruthy();   // unknown targets never move the user
  });

  it("falls back to the first tab when the selected one disappears and honours `initial`", () => {
    const { rerender } = render(<WorkspaceTabs tabs={tabs} panels={panels} ariaLabel="Разделы" initial="b" />);
    expect(screen.getByText("Содержимое B")).toBeTruthy();
    rerender(<WorkspaceTabs tabs={tabs.filter((tab) => tab.id !== "b")} panels={panels} ariaLabel="Разделы" initial="b" />);
    expect(screen.getByText("Содержимое A")).toBeTruthy();
  });
});

describe("WorkspaceTabs keepMounted", () => {
  it("keeps a visited keepMounted panel alive (hidden) so its state survives a tab switch", () => {
    const panels2 = { a: <p>A</p>, b: <input aria-label="draft" /> };
    render(<WorkspaceTabs tabs={tabs.slice(0, 2)} panels={panels2} ariaLabel="Разделы" keepMounted={["b"]} />);
    expect(screen.queryByLabelText("draft")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Вторая" }));
    fireEvent.change(screen.getByLabelText("draft"), { target: { value: "черновик" } });
    fireEvent.click(screen.getByRole("button", { name: "Первая" }));
    expect((document.querySelector("input[aria-label=draft]") as HTMLInputElement).value).toBe("черновик");
    expect(document.querySelector("input[aria-label=draft]")!.closest("[hidden]")).not.toBeNull();
  });
});
