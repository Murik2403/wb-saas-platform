import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { TodayView } from "./App";

vi.mock("./data-source", async (load) => ({
  ...await load<typeof import("./data-source")>(),
  getIncidentOperations: vi.fn(async () => ({ cases: [], summary: {} })),
}));
beforeEach(() => window.localStorage.clear());
afterEach(cleanup);

const props = { actions: [], chartBars: [], chartBarsWeek: [], inventory: [], onAction: vi.fn(), period: "day" as const, setPeriod: vi.fn(), todayMetrics: [] };

describe("Today tabs", () => {
  it("shows exactly the blocks of the selected tab", () => {
    render(<TodayView {...props} />);
    expect(screen.getByText("Что требует действия")).toBeInTheDocument();
    expect(screen.queryByText("Остатки, маржа, сигнал")).toBeNull();
    expect(screen.queryByText("Инциденты и компенсации")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Остатки и маржа" }));
    expect(screen.getByText("Остатки, маржа, сигнал")).toBeInTheDocument();
    expect(screen.queryByText("Что требует действия")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Инциденты" }));
    expect(screen.getByText("Инциденты и компенсации")).toBeInTheDocument();
    expect(screen.queryByText("Остатки, маржа, сигнал")).toBeNull();
  });
  it("opens the tab that owns a deep-link target and remembers the last tab", () => {
    const first = render(<TodayView {...props} target="wb-incidents" />);
    expect(screen.getByText("Инциденты и компенсации")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Остатки и маржа" }));
    first.unmount();
    render(<TodayView {...props} />);
    expect(screen.getByText("Остатки, маржа, сигнал")).toBeInTheDocument();
  });
  it("keeps typed incident input when switching tabs", async () => {
    render(<TodayView {...props} />);
    fireEvent.click(screen.getByRole("button", { name: "Инциденты" }));
    fireEvent.click(screen.getByText("Создать инцидент или записать компенсацию"));
    fireEvent.change(await screen.findByLabelText("Название"), { target: { value: "Недостача" } });
    fireEvent.click(screen.getByRole("button", { name: "Сегодня" }));
    expect(screen.getByLabelText("Название")).not.toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Инциденты" }));
    expect((screen.getByLabelText("Название") as HTMLInputElement).value).toBe("Недостача");
  });
});
