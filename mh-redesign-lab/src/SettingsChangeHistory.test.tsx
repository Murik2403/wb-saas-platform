import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import {currentShop} from "./selected-shop";
vi.mock("./selected-shop",()=>({currentShop:vi.fn(()=>null)}));
import { SettingsChangeHistory } from "./SettingsChangeHistory";
import { getSettingsChangeHistory, type SettingsChangeHistoryItem } from "./data-source";

vi.mock("./data-source", () => ({ getSettingsChangeHistory: vi.fn() }));

const history = vi.mocked(getSettingsChangeHistory);
const entry: SettingsChangeHistoryItem = {
  id: 1,
  kind: "cost" as const,
  nm_id: 308044778,
  before: { cost_per_wb_unit: 120, supplier_article: "OLD" },
  after: { cost_per_wb_unit: 145.5, supplier_article: "NEW" },
  changed_at: "2026-09-30T12:30:00+03:00",
  source: "tenant_api",
  actor: "owner@example.test",
  reason: "Новая закупочная цена",
};

function page(items = [entry], total = items.length, offset = 0) {
  return Promise.resolve({ items, total, limit: 25, offset });
}

describe("SettingsChangeHistory", () => {
  beforeEach(() => history.mockReset());

  it("renders known cost fields as readable before and after values with source and actor", async () => {
    history.mockReturnValue(page());
    render(<SettingsChangeHistory />);

    expect(await screen.findByText("Себестоимость единицы WB")).toBeInTheDocument();
    expect(screen.getByText("120 ₽ → 145,5 ₽")).toBeInTheDocument();
    expect(screen.getByText("OLD → NEW")).toBeInTheDocument();
    expect(document.querySelector(".settings-history-meta")).toHaveTextContent("owner@example.test");
    expect(screen.getByText("Причина: Новая закупочная цена")).toBeInTheDocument();
    expect(screen.getByText(/30 сент\. 2026/)).toBeInTheDocument();
    expect(screen.getByText(/История ведётся с момента включения учёта/)).toBeInTheDocument();
  });

  it("labels an initial value as creation and does not expose raw JSON", async () => {
    history.mockReturnValue(page([{ ...entry, before: null, after: { material_per_unit: 0.42, enabled: true }, kind: "production_norm" }]));
    render(<SettingsChangeHistory />);

    expect(await screen.findAllByText("Создано значение")).toHaveLength(2);
    expect(screen.getByText("Расход материала на комплект")).toBeInTheDocument();
    expect(screen.getByText("0,42")).toBeInTheDocument();
    expect(screen.getByText("Активность нормы")).toBeInTheDocument();
    expect(screen.queryByText(/\{"material_per_unit"/)).not.toBeInTheDocument();
  });

  it("shows very small numeric changes without rounding them away", async () => {
    history.mockReturnValue(page([{ ...entry, kind: "production_norm", before: { material_per_unit: 0.01 }, after: { material_per_unit: 0.010001 } }]));
    render(<SettingsChangeHistory />);

    expect(await screen.findByText("Расход материала на комплект")).toBeInTheDocument();
    expect(screen.getByText("0,01 → 0,010001")).toBeInTheDocument();
  });

  it("applies type and product filters and resets pagination to the first page", async () => {
    history.mockImplementation((query = {}) => Promise.resolve((query.offset ?? 0) === 0 && !query.kind
      ? { items: [entry], total: 40, limit: 25, offset: 0 }
      : query.kind
        ? { items: [entry], total: 2, limit: 25, offset: 0 }
        : { items: [entry], total: 40, limit: 25, offset: 25 }));
    render(<SettingsChangeHistory />);
    await screen.findByText((_, node) => node?.classList.contains("settings-history-meta") ?? false);
    fireEvent.click(screen.getByRole("button", { name: "Следующая" }));
    await waitFor(() => expect(history).toHaveBeenLastCalledWith({ limit: 25, offset: 25 }));

    fireEvent.change(screen.getByLabelText("Тип изменения"), { target: { value: "production_norm" } });
    fireEvent.change(screen.getByLabelText("Артикул WB"), { target: { value: "308044778" } });
    fireEvent.click(screen.getByRole("button", { name: "Применить фильтры" }));

    await waitFor(() => expect(history).toHaveBeenLastCalledWith({ kind: "production_norm", nm_id: 308044778, limit: 25, offset: 0 }));
  });

  it("refreshes, retries failures, and displays an empty result", async () => {
    let attempts = 0;
    history.mockImplementation(() => ++attempts === 1 ? Promise.reject(new Error("Временно недоступно")) : page([], 0));
    render(<SettingsChangeHistory />);

    expect(await screen.findByRole("alert")).toHaveTextContent("Временно недоступно");
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByText("Изменений пока нет")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Обновить историю" }));
    await waitFor(() => expect(history).toHaveBeenCalledTimes(3));
  });

  it("returns to the newest page after a successful settings save triggers refresh", async () => {
    history.mockImplementation((query = {}) => Promise.resolve({ items: [entry], total: 40, limit: 25, offset: query.offset ?? 0 }));
    const { rerender } = render(<SettingsChangeHistory refreshKey={0} />);
    await screen.findByText((_, node) => node?.classList.contains("settings-history-meta") ?? false);
    fireEvent.click(screen.getByRole("button", { name: "Следующая" }));
    await waitFor(() => expect(history).toHaveBeenLastCalledWith({ limit: 25, offset: 25 }));

    rerender(<SettingsChangeHistory refreshKey={1} />);
    await waitFor(() => expect(history).toHaveBeenLastCalledWith({ limit: 25, offset: 0 }));
  });
});

it("opens secondary settings with explicit cost scope without manual filtering",async()=>{
 vi.mocked(currentShop).mockReturnValue({id:2,name:'Secondary',active:true,can_write:true,verified_identity:'a'.repeat(64),snapshot_revision:1,allowed_actions:[]});history.mockReturnValue(page());
 render(<SettingsChangeHistory/>);await screen.findByText("120 ₽ → 145,5 ₽");
 expect(history.mock.calls[0][0]).toMatchObject({kind:'cost'});expect(screen.queryByRole('option',{name:'Все изменения'})).not.toBeInTheDocument();
 expect(screen.getByText(/Нормы производства общие/)).toBeInTheDocument();
 fireEvent.change(screen.getByLabelText('Тип изменения'),{target:{value:'production_norm'}});fireEvent.click(screen.getByText('Применить фильтры'));
 await waitFor(()=>expect(history.mock.calls.at(-1)![0]).toMatchObject({kind:'production_norm'}));
});
