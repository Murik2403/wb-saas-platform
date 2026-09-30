import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import type { ActionRecommendation } from "./contracts";
import { TodayView } from "./App";

vi.mock("./data-source", async (load) => ({
  ...await load<typeof import("./data-source")>(),
  getIncidentOperations: vi.fn(async () => ({ cases: [], summary: {} })),
}));
afterEach(cleanup);

describe("Today operational signal and queue", () => {
  it("shows the primary signal once initially while keeping its action and all queue entries accessible", () => {
    const main: ActionRecommendation = { id: "a", sku: "SKU-A", title: "Проверить партию", issue: "Первое замечание", owner: "Главное действие", impact: "", due: "сегодня", tone: "warn", iconKey: "stock" };
    const other = { ...main, id: "b", sku: "SKU-B", issue: "Второе замечание", title: "Другое действие", owner: "Другой исполнитель" };
    const onAction = vi.fn();
    render(<TodayView actions={[main, other]} topAction={main} chartBars={[]} chartBarsWeek={[]} inventory={[]}
      onAction={onAction} period="day" setPeriod={vi.fn()} todayMetrics={[]} />);
    expect(screen.getAllByText("Первое замечание")).toHaveLength(1);
    expect(screen.getByText("Второе замечание")).toBeVisible();
    expect(screen.getByText("2 в очереди")).toBeVisible();
    fireEvent.click(within(screen.getByRole("region", { name: "Главный сигнал" })).getByRole("button", { name: "Открыть действие" }));
    expect(onAction).toHaveBeenCalledWith("Проверить партию", "SKU-A");
    fireEvent.click(screen.getByRole("button", { name: "Показать всю очередь · 2" }));
    expect(screen.getAllByText("Первое замечание")).toHaveLength(2);
    expect(screen.getByText("Второе замечание")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Свернуть очередь" }));
    expect(screen.getAllByText("Первое замечание")).toHaveLength(1);
  });
});
