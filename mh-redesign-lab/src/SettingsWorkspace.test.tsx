import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { SettingsWorkspace } from "./SettingsWorkspace";
import { BackupHistory } from "./SyncHistory";

afterEach(cleanup);

describe("Settings categories", () => {
  it("reveals a targeted production section without discarding another category draft", () => {
    const props = { connections: <input aria-label="Токен" defaultValue="" />, data: null, production: <h2>Нормы материала</h2>, backups: null };
    const { rerender } = render(<SettingsWorkspace {...props} />);
    fireEvent.change(screen.getByRole("textbox", { name: "Токен" }), { target: { value: "черновик" } });
    rerender(<SettingsWorkspace {...props} sectionTarget="production-norms" />);
    expect(screen.getByRole("heading", { name: "Нормы материала" })).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Подключения" }));
    expect(screen.getByRole("textbox", { name: "Токен" })).toHaveValue("черновик");
  });
  it("shows connections initially and preserves unfinished data forms when navigating", () => {
    render(<SettingsWorkspace connections={<h2>Статус API</h2>} data={<input aria-label="Черновик CSV" defaultValue="" />}
      production={<h2>Нормы материала</h2>} backups={<h2>Список копий</h2>} />);
    expect(screen.getByRole("heading", { name: "Статус API" })).toBeVisible();
    expect(screen.queryByRole("textbox", { name: "Черновик CSV" })).toBeNull();
    const dataButton = screen.getByRole("button", { name: "Данные" });
    fireEvent.click(dataButton);
    expect(dataButton).toHaveAttribute("aria-pressed", "true");
    fireEvent.change(screen.getByRole("textbox", { name: "Черновик CSV" }), { target: { value: "не сохранено" } });
    fireEvent.click(screen.getByRole("button", { name: "Производство" }));
    expect(screen.getByRole("heading", { name: "Нормы материала" })).toBeVisible();
    expect(screen.queryByRole("textbox", { name: "Черновик CSV" })).toBeNull();
    fireEvent.click(dataButton);
    expect(screen.getByRole("textbox", { name: "Черновик CSV" })).toHaveValue("не сохранено");
    fireEvent.click(screen.getByRole("button", { name: "Резервные копии" }));
    expect(screen.getByRole("heading", { name: "Список копий" })).toBeVisible();
  });

  it("supports native keyboard selection and excludes hidden controls from tab order", async () => {
    const user = userEvent.setup();
    render(<SettingsWorkspace connections={<button>Подключить</button>} data={<input aria-label="CSV" />}
      production={null} backups={null} />);
    await user.tab();
    expect(screen.getByRole("button", { name: "Подключения" })).toHaveFocus();
    await user.tab(); await user.keyboard("{Enter}");
    expect(screen.getByRole("button", { name: "Данные" })).toHaveAttribute("aria-pressed", "true");
    await user.tab(); await user.tab(); await user.tab();
    expect(screen.getByRole("textbox", { name: "CSV" })).toHaveFocus();
  });
});

describe("Backup list disclosure", () => {
  it("shows newest three without mutating data, and keeps older archives in a closed disclosure", () => {
    const backups = Array.from({ length: 5 }, (_, i) => ({ name: `archive-${i + 1}.zip`, modified_at: `2026-09-${20 + i}T10:00:00`, size_bytes: 1048576 }));
    const original = backups.map((b) => b.name);
    const { container } = render(<BackupHistory backups={backups} />);
    const latest = container.querySelector(".backup-history")!;
    expect(within(latest as HTMLElement).getAllByText(/archive-/).map((node) => node.textContent)).toEqual(["archive-5.zip", "archive-4.zip", "archive-3.zip"]);
    const details = container.querySelector("details")!;
    expect(details).not.toHaveAttribute("open");
    expect(within(details).getByText("archive-1.zip")).toBeInTheDocument();
    fireEvent.click(screen.getByText("Все резервные копии · ещё 2"));
    expect(details).toHaveAttribute("open");
    expect(backups.map((b) => b.name)).toEqual(original);
  });
});
