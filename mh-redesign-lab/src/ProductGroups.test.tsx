import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { ProductsView } from "./App";
import * as ds from "./data-source";

let store: ds.ProductGroup[] = [];
let nextId = 1;
const mk = (name: string, nmIds: number[]): ds.ProductGroup => ({ id: nextId++, name, nmIds, count: nmIds.length });

vi.mock("./data-source", async (load) => ({
  ...await load<typeof import("./data-source")>(),
  getPriceUploadHistory: vi.fn(async () => []),
  getProductsPrevious: vi.fn(async () => { throw new Error("off"); }),
  getProductsTrend: vi.fn(async () => { throw new Error("off"); }),
  getProductGroups: vi.fn(async () => store.map((g) => ({ ...g }))),
  createProductGroup: vi.fn(async (name: string, ids: number[]) => {
    if (store.some((g) => g.name.toLowerCase() === name.toLowerCase())) throw new Error("Группа с таким названием уже есть");
    const g = mk(name, ids); store.push(g); return g;
  }),
  updateProductGroup: vi.fn(async (id: number, name: string, ids: number[]) => {
    const g = store.find((x) => x.id === id)!; g.name = name; g.nmIds = ids; g.count = ids.length; return g;
  }),
  archiveProductGroup: vi.fn(async (id: number) => { const g = store.find((x) => x.id === id)!; store = store.filter((x) => x.id !== id); return g; }),
}));

const row = (nmId: string, article: string): ds.ProductEconomicsRow => ({
  nmId, article, product: `Товар ${article}`, orders: 1, sales: 1, revenue: 100, buyout: 100, adSpend: 0, drr: 0, stock: 1,
  dailySales: 1, daysOfStock: 5, unitCost: 1, margin: 10, marginPct: 10,
});
const products = [row("1", "AAA"), row("2", "BBB"), row("3", "CCC")];
const view = () => render(<ProductsView period={{ id: "30d" }} products={products} prices={[]} productCards={[]} productCardErrors={[]} productPriceErrors={[]}
  customerQuality={null} onRefresh={vi.fn(async () => {})} setPeriod={vi.fn()} onOpenProduct={vi.fn()} />);
const rowsText = () => document.body.textContent ?? "";
const filter = () => screen.getByLabelText("Группа") as HTMLSelectElement;
const hasSku = (a: string) => screen.queryAllByRole("button", { name: new RegExp(`Товар ${a}`) }).length > 0;

beforeEach(() => { store = []; nextId = 1; window.localStorage.clear(); });
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

describe("Product groups", () => {
  it("creates a group from the picker, then the filter narrows the products", async () => {
    view();
    fireEvent.click(screen.getByText(/Группы товаров/));
    fireEvent.click(screen.getByRole("button", { name: "Новая группа" }));
    fireEvent.change(screen.getByLabelText("Название группы"), { target: { value: "Лето" } });
    const picker = within(screen.getByRole("list", { name: "Товары для группы" }));
    fireEvent.click(picker.getAllByRole("checkbox")[0]);
    fireEvent.click(picker.getAllByRole("checkbox")[2]);
    expect(screen.getByText("Выбрано: 2")).toBeVisible();
    fireEvent.click(screen.getByRole("button", { name: "Создать группу" }));
    await waitFor(() => expect(filter()).toBeInTheDocument());
    expect(hasSku("BBB")).toBe(true);
    fireEvent.change(filter(), { target: { value: "1" } });
    expect(hasSku("AAA")).toBe(true);
    expect(hasSku("BBB")).toBe(false);
    expect(hasSku("CCC")).toBe(true);
    expect(window.localStorage.getItem("mh-product-group-filter")).toBe("1");
  });

  it("search in the picker narrows the SKU list and a duplicate name shows the server message", async () => {
    store = [mk("Лето", [1])];
    view();
    await waitFor(() => expect(filter()).toBeInTheDocument());
    fireEvent.click(screen.getByText(/Группы товаров/));
    fireEvent.click(screen.getByRole("button", { name: "Новая группа" }));
    fireEvent.change(screen.getByLabelText("Найти товар"), { target: { value: "bbb" } });
    expect(within(screen.getByRole("list", { name: "Товары для группы" })).getAllByRole("checkbox")).toHaveLength(1);
    fireEvent.change(screen.getByLabelText("Название группы"), { target: { value: "лето" } });
    fireEvent.click(screen.getByRole("button", { name: "Создать группу" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Группа с таким названием уже есть");
  });

  it("renames and changes members of a group", async () => {
    store = [mk("Лето", [1])];
    view();
    await waitFor(() => expect(filter()).toBeInTheDocument());
    fireEvent.click(screen.getByText(/Группы товаров/));
    fireEvent.click(screen.getByRole("button", { name: "Изменить группу Лето" }));
    fireEvent.change(screen.getByLabelText("Название группы"), { target: { value: "Зима" } });
    fireEvent.click(within(screen.getByRole("list", { name: "Товары для группы" })).getAllByRole("checkbox")[1]);
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(screen.getByRole("option", { name: "Зима (2)" })).toBeInTheDocument());
  });

  it("archive removes the group and a selected group falls back to all products", async () => {
    store = [mk("Лето", [1])];
    window.localStorage.setItem("mh-product-group-filter", "1");
    view();
    await waitFor(() => expect(filter().value).toBe("1"));
    expect(hasSku("BBB")).toBe(false);
    fireEvent.click(screen.getByText(/Группы товаров/));
    fireEvent.click(screen.getByRole("button", { name: "Архивировать группу Лето" }));
    await waitFor(() => expect(screen.queryByLabelText("Группа")).toBeNull());
    expect(hasSku("BBB")).toBe(true);
    expect(window.localStorage.getItem("mh-product-group-filter")).toBe("all");
  });

  it("a remembered group that no longer exists falls back to all products", async () => {
    store = [mk("Лето", [1])];
    window.localStorage.setItem("mh-product-group-filter", "77");
    view();
    await waitFor(() => expect(filter().value).toBe("all"));
    expect(hasSku("BBB")).toBe(true);
  });

  it("works when localStorage throws", async () => {
    store = [mk("Лето", [1])];
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => { throw new Error("blocked"); });
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => { throw new Error("blocked"); });
    view();
    await waitFor(() => expect(filter()).toBeInTheDocument());
    fireEvent.change(filter(), { target: { value: "1" } });
    expect(filter().value).toBe("1");
    expect(hasSku("BBB")).toBe(false);
    expect(rowsText()).toContain("Группы товаров");
  });
});
