import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsWorkspace } from "./SettingsWorkspace";

beforeEach(() => window.localStorage.clear());
afterEach(cleanup);

const view = (sectionTarget?: string | null) => render(<SettingsWorkspace sectionTarget={sectionTarget}
  connections={<h2>Статус API</h2>} data={<h2>Синхронизация и журнал</h2>} production={<h2>Нормы материала</h2>} backups={<h2>Резервные копии</h2>} />);

describe("Settings tabs", () => {
  it("shows backups inside the Данные tab and nothing from other tabs", async () => {
    view();
    expect(screen.getAllByRole("button").map((b) => b.textContent)).toEqual(["Подключения", "Данные", "Производство"]);
    await userEvent.click(screen.getByRole("button", { name: "Данные" }));
    expect(screen.getByRole("heading", { name: "Синхронизация и журнал" })).toBeVisible();
    expect(screen.getByRole("heading", { name: "Резервные копии" })).toBeVisible();
    expect(screen.queryByRole("heading", { name: "Нормы материала" })).toBeNull();
  });

  it("opens the tab that owns a deep-link target", () => {
    view("material-inventory");
    expect(screen.getByRole("button", { name: "Производство" })).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("heading", { name: "Нормы материала" })).toBeVisible();
  });

  it("remembers the last tab", async () => {
    view();
    await userEvent.click(screen.getByRole("button", { name: "Данные" }));
    cleanup();
    view();
    expect(screen.getByRole("button", { name: "Данные" })).toHaveAttribute("aria-pressed", "true");
    expect(window.localStorage.getItem("mh.tab.settings")).toBe("data");
  });
});
