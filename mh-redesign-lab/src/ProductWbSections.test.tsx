import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { ProductCardsPanel } from "./ProductCardsPanel";
import { ShopQualityPanel, type ShopQualityActions } from "./ShopQualityPanel";
import { ProductPricesPanel } from "./ProductPricesPanel";
import type { CustomerQualityData, ProductCard, ProductPriceRow } from "./data-source";

afterEach(cleanup);

const card = (n: number, over: Partial<ProductCard> = {}): ProductCard => ({
  nmId: n, vendorCode: `ART-${n}`, title: `Наклейки ${n}`, brand: "Бренд", subject: "Наклейки", photos: [], sizes: 1, ...over,
});

describe("ProductCardsPanel", () => {
  it("shows a counter, pages through cards, filters and opens the editor", () => {
    const onEdit = vi.fn();
    const cards = Array.from({ length: 30 }, (_, i) => card(i + 1));
    render(<ProductCardsPanel cards={cards} errors={[]} onEdit={onEdit} />);
    expect(screen.getByText("Показано 12 из 30")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Показать ещё 12" }));
    expect(screen.getByText("Показано 24 из 30")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Показать ещё 6" }));
    expect(screen.queryByRole("button", { name: /Показать ещё/ })).toBeNull();

    fireEvent.change(screen.getByRole("searchbox"), { target: { value: "ART-7" } });
    expect(screen.getByText("Показано 1 из 1 (всего 30)")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: /Наклейки 7/ }));
    expect(onEdit).toHaveBeenCalledWith(cards[6]);
  });

  it("uses the right plural for sizes and explains empty states", () => {
    render(<ProductCardsPanel cards={[card(1, { sizes: 1 }), card(2, { sizes: 3 }), card(3, { sizes: 5 })]} errors={[]} onEdit={() => undefined} />);
    expect(screen.getByText(/· 1 размер$/)).toBeTruthy();
    expect(screen.getByText(/· 3 размера$/)).toBeTruthy();
    expect(screen.getByText(/· 5 размеров$/)).toBeTruthy();
    cleanup();
    render(<ProductCardsPanel cards={[]} errors={["HTTP 409"]} onEdit={() => undefined} />);
    expect(screen.getByText("После исправления доступа обновите раздел.")).toBeTruthy();
  });
});

const actions = (): ShopQualityActions => ({ onRefresh: vi.fn(), onAnswerFeedback: vi.fn(), onAnswerQuestion: vi.fn(), onReplyChat: vi.fn(), onReturn: vi.fn() });
const feedback = (id: string, over: Record<string, unknown> = {}) => ({
  id, text: "Хорошие салфетки", productValuation: 4, createdDate: "2026-09-28T10:15:00Z",
  productDetails: { nmId: 11, productName: "Салфетки на стол", supplierArticle: "NAP-1" }, ...over,
});
const quality = (over: Partial<CustomerQualityData> = {}): CustomerQualityData => ({
  rating: {}, unread: {}, feedbacks: [], questions: [], quarantine: [], returns: { claims: [], total: 0 }, return_history: [], chats: [], errors: [], ...over,
});

describe("ShopQualityPanel", () => {
  it("names the product from productDetails and shows stars and date", () => {
    render(<ShopQualityPanel data={quality({ feedbacks: [feedback("f1")] })} actions={actions()} />);
    expect(screen.getByText("Салфетки на стол")).toBeTruthy();
    expect(screen.getByText(/★★★★☆ · 28\.09\.2026 10:15/)).toBeTruthy();
    expect(screen.queryByText("Отзыв · WB")).toBeNull();
  });

  it("falls back to pros/cons when the review has no text", () => {
    render(<ShopQualityPanel data={quality({ feedbacks: [feedback("f1", { text: "", pros: "Красиво", cons: "Дорого" })] })} actions={actions()} />);
    expect(screen.getByText("Плюсы: Красиво · Минусы: Дорого")).toBeTruthy();
  });

  it("shows why data is missing instead of a silent zero", () => {
    render(<ShopQualityPanel data={quality({ errors: ["rating: у токена нет доступа к категории «Отзывы/Вопросы»."] })} actions={actions()} />);
    expect(screen.getByText("у токена нет доступа к категории «Отзывы/Вопросы».")).toBeTruthy();
    expect(screen.getAllByText("—").length).toBeGreaterThan(0);
  });

  it("limits long lists, expands them, and routes the reply action", () => {
    const a = actions();
    const many = Array.from({ length: 8 }, (_, i) => feedback(`f${i}`, { text: `Текст ${i}` }));
    render(<ShopQualityPanel data={quality({ feedbacks: many })} actions={a} />);
    expect(screen.getAllByRole("button", { name: "Ответить" })).toHaveLength(5);
    fireEvent.click(screen.getByRole("button", { name: "Показать все 8" }));
    expect(screen.getAllByRole("button", { name: "Ответить" })).toHaveLength(8);
    fireEvent.click(screen.getAllByRole("button", { name: "Ответить" })[2]);
    expect(a.onAnswerFeedback).toHaveBeenCalledWith("f2");
    fireEvent.click(screen.getByRole("button", { name: "Обновить данные качества магазина" }));
    expect(a.onRefresh).toHaveBeenCalled();
  });

  it("routes return decisions and says so when everything is empty", () => {
    const a = actions();
    render(<ShopQualityPanel data={quality({ returns: { claims: [{ id: "r1", nm_id: 5, user_comment: "Не подошёл" }], total: 1 } })} actions={a} />);
    fireEvent.click(screen.getByRole("button", { name: "Отклонить" }));
    expect(a.onReturn).toHaveBeenCalledWith("r1", "reject");
    cleanup();
    render(<ShopQualityPanel data={quality()} actions={a} />);
    expect(screen.getByText("Новых отзывов, вопросов, чатов и обращений нет.")).toBeTruthy();
  });
});

const price = (n: number, over: Partial<ProductPriceRow> = {}): ProductPriceRow => ({
  nmId: n, vendorCode: `SKU-${n}`, price: 5990, discount: 59, clubDiscount: 0, discountedPrice: 2456, ...over,
});
const priceProps = (over: Record<string, unknown> = {}) => ({
  prices: [price(1), price(2)], query: "", drafts: {}, onDraft: vi.fn(), onSubmit: vi.fn(), busy: false,
  message: "", uploadStatus: "", results: [], history: [], hasErrors: false, ...over,
});

describe("ProductPricesPanel", () => {
  it("renders a real table with the discounted price and a disabled send button when nothing changed", () => {
    render(<ProductPricesPanel {...priceProps()} />);
    expect(screen.getByRole("columnheader", { name: "Скидка, %" })).toBeTruthy();
    expect(screen.getAllByText("2 456 ₽")).toHaveLength(2);
    expect(screen.getByText("Изменений нет")).toBeTruthy();
    expect((screen.getByRole("button", { name: "Отправить изменения в WB" }) as HTMLButtonElement).disabled).toBe(true);
  });

  it("reports edits, counts changed SKUs and sends only on click", () => {
    const p = priceProps({ drafts: { 1: { price: 4990, discount: 40 } } });
    render(<ProductPricesPanel {...p} />);
    expect(screen.getByText("Изменено: 1 SKU. В WB уйдёт только изменённое.")).toBeTruthy();
    expect(screen.getByText("2 994 ₽")).toBeTruthy(); // 4990 * 0.6
    fireEvent.change(screen.getByLabelText("Цена SKU-2"), { target: { value: "6000" } });
    expect(p.onDraft).toHaveBeenCalledWith(2, { price: 6000, discount: 59 });
    fireEvent.click(screen.getByRole("button", { name: "Отправить изменения в WB" }));
    expect(p.onSubmit).toHaveBeenCalledTimes(1);
  });

  it("blocks sending while an upload is in progress, filters by query and pages long lists", () => {
    const many = Array.from({ length: 120 }, (_, i) => price(i + 1));
    const { rerender } = render(<ProductPricesPanel {...priceProps({ prices: many, drafts: { 1: { price: 1, discount: 0 } }, busy: true })} />);
    expect((screen.getByRole("button", { name: "Отправить изменения в WB" }) as HTMLButtonElement).disabled).toBe(true);
    expect(screen.getByText("Показано 50 из 120")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Показать ещё 50" }));
    expect(screen.getByText("Показано 100 из 120")).toBeTruthy();
    rerender(<ProductPricesPanel {...priceProps({ prices: many, query: "SKU-77" })} />);
    expect(within(screen.getByRole("table")).getAllByRole("row")).toHaveLength(2);
  });

  it("explains an empty table and shows upload history", () => {
    render(<ProductPricesPanel {...priceProps({ prices: [], hasErrors: true, history: [{ wb_upload_id: 9, created_at: "2026-09-30 10:00", goods_count: 3, status: "Завершена" }] })} />);
    expect(screen.getByText("После исправления доступа обновите раздел.")).toBeTruthy();
    expect(screen.getByText("WB загрузка №9")).toBeTruthy();
  });
});
