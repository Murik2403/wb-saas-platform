import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { lazyLoaded } from "./test-lazy";
import userEvent from "@testing-library/user-event";
import { ProductsView } from "./App";
import * as ds from "./data-source";

vi.mock("./data-source", async (load) => ({
  ...await load<typeof import("./data-source")>(),
  getPriceUploadHistory: vi.fn(async () => []),
  getProductsPrevious: vi.fn(async () => ({ start: "", end: "", hasData: false, totals: null })),
}));
beforeEach(() => window.localStorage.clear());
afterEach(cleanup);

const price: ds.ProductPriceRow = { nmId: 7, vendorCode: "V7", price: 1000, discount: 10, clubDiscount: 0, discountedPrice: 900 };
const view = (target?: string | null) => render(<ProductsView period={{ id: "30d" }} products={[]} prices={[price]} productCards={[]} productCardErrors={[]} productPriceErrors={[]}
  customerQuality={null} onRefresh={vi.fn(async () => {})} setPeriod={vi.fn()} onOpenProduct={vi.fn()} target={target} />);
const BLOCKS: Array<[string, () => HTMLElement | null]> = [
  ["Товары по артикулам", () => screen.queryByRole("heading", { name: "Товары по артикулам" })],
  ["Тепловая карта", () => screen.queryByRole("region", { name: "Тепловая карта товаров по дням" })],
  ["План-факт по артикулам (РНП)", () => screen.queryByRole("heading", { name: "План-факт по артикулам (РНП)" })],
  ["Карточки и медиа", () => screen.queryByRole("heading", { name: "Карточки и медиа" })],
  ["Качество магазина", () => screen.queryByRole("heading", { name: "Качество магазина" })],
  ["Рекомендации по ценам", () => screen.queryByRole("heading", { name: "Рекомендации по ценам" })],
  ["Цены и скидки", () => screen.queryByRole("heading", { name: "Цены и скидки" })],
];
const shown = () => BLOCKS.filter(([, find]) => find()).map(([name]) => name);

describe("Products tabs", () => {
  it.each([
    ["Товары", ["Товары по артикулам"]],
    ["Аналитика", ["Тепловая карта", "План-факт по артикулам (РНП)"]],
    ["Контент и качество", ["Карточки и медиа", "Качество магазина"]],
    ["Цены", ["Рекомендации по ценам", "Цены и скидки"]],
  ])("tab %s shows exactly its blocks", async (tab, blocks) => {
    view();
    await userEvent.click(screen.getByRole("button", { name: tab }));
    await lazyLoaded();
    expect(shown()).toEqual(blocks);
  });

  it("opens the tab that owns a deep-link target", async () => {
    view("price-uploads");
    await lazyLoaded();
    expect(screen.getByRole("button", { name: "Цены" })).toHaveAttribute("aria-pressed", "true");
    expect(shown()).toContain("Цены и скидки");
  });

  it("remembers the last tab", async () => {
    view();
    await userEvent.click(screen.getByRole("button", { name: "Аналитика" }));
    cleanup();
    view();
    expect(screen.getByRole("button", { name: "Аналитика" })).toHaveAttribute("aria-pressed", "true");
    expect(window.localStorage.getItem("mh.tab.products")).toBe("analytics");
  });

  it("keeps the price draft while switching Цены -> Товары -> Цены", async () => {
    view();
    await userEvent.click(screen.getByRole("button", { name: "Цены" }));
    await lazyLoaded();
    const input = screen.getByLabelText("Цена V7");
    await userEvent.clear(input);
    await userEvent.type(input, "1234");
    await userEvent.click(screen.getByRole("button", { name: "Товары" }));
    await userEvent.click(screen.getByRole("button", { name: "Цены" }));
    expect(screen.getByLabelText("Цена V7")).toHaveValue(1234);
  });
});
