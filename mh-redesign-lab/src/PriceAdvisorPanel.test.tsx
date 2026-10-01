import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { PriceAdvisorPanel } from "./PriceAdvisorPanel";
import type { PriceAdviceItem, ProductPriceRow } from "./data-source";

const prices: ProductPriceRow[] = [{ nmId: 1, vendorCode: "A-1", price: 1000, discount: 10, clubDiscount: 0, discountedPrice: 900 }];
const item = (over: Partial<PriceAdviceItem> = {}): PriceAdviceItem => ({
  nm_id: 1, vendor_code: "A-1", current_price: 1000, current_discount: 10, current_final: 900, recommended_price: 1150, recommended_final: 1035,
  est_margin_now_pct: 10, est_margin_after_pct: 19.1, breakeven_final: 875, reason: "Причина: комиссия 20%", status: "recommend", note: "Маржа 10% → 19.1%", ...over,
});
const body = (items: PriceAdviceItem[]) => ({ target_margin: 20, max_step_pct: 15, min_sales: 10, disclaimer: "оценка, спрос на цену не прогнозируется", items });
const ok = (data: unknown) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(data) });
const period = { id: "30d" as const };

beforeEach(() => { window.localStorage.clear(); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("PriceAdvisorPanel", () => {
  it("is lazy, shows the safety note, never preselects, applies only chosen rows to the draft", async () => {
    const fetchMock = vi.fn(() => ok(body([item(), item({ nm_id: 2, vendor_code: "B-2", status: "below_breakeven", recommended_price: 700, current_discount: 0 })])));
    vi.stubGlobal("fetch", fetchMock);
    const onApply = vi.fn();
    render(<PriceAdvisorPanel period={period} prices={prices} onApply={onApply} />);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(screen.getByText(/Ничего не отправляется в WB/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать рекомендации" }));
    expect(await screen.findByText("A-1")).toBeInTheDocument();
    const [url, init] = fetchMock.mock.calls[0] as unknown as [string, RequestInit];
    expect(url).toContain("/api/v1/dashboard/products/price-advice?period=30d&target_margin=20");
    expect(JSON.parse(String(init.body))).toEqual({ prices: [{ nm_id: 1, price: 1000, discount: 10 }] });
    expect(screen.getByText("Ниже безубыточности", { selector: ".status-pill" })).toBeInTheDocument();
    const boxes = screen.getAllByRole("checkbox") as HTMLInputElement[];
    expect(boxes.every((box) => !box.checked)).toBe(true);
    const apply = screen.getByRole("button", { name: /Применить выбранные к черновику/ });
    expect(apply).toBeDisabled();
    fireEvent.click(screen.getByLabelText("Применить к черновику A-1"));
    fireEvent.click(apply);
    expect(onApply).toHaveBeenCalledWith([{ nmId: 1, price: 1150, discount: 10 }]);
    expect(await screen.findByText(/В WB ничего не отправлено/)).toBeInTheDocument();
    expect(fetchMock).toHaveBeenCalledTimes(1); // no upload endpoint was ever touched
  });

  it("hides ok/insufficient rows until asked and shows the reason text", async () => {
    vi.stubGlobal("fetch", vi.fn(() => ok(body([item(), item({ nm_id: 3, vendor_code: "C-3", status: "insufficient_data", recommended_price: null, reason: "Мало продаж за период: 3 < 10" })]))));
    render(<PriceAdvisorPanel period={period} prices={prices} onApply={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать рекомендации" }));
    await screen.findByText("A-1");
    expect(screen.queryByText("C-3")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Показать остальные (1)" }));
    expect(screen.getByText("C-3")).toBeInTheDocument();
    expect(screen.getAllByRole("checkbox")).toHaveLength(1);
    expect(screen.getByText("Мало продаж за период: 3 < 10")).toBeInTheDocument();
  });

  it("validates and remembers the target margin, shows an error state", async () => {
    const fetchMock = vi.fn(() => Promise.resolve({ ok: false, status: 500, json: () => Promise.resolve({}) }));
    vi.stubGlobal("fetch", fetchMock);
    render(<PriceAdvisorPanel period={period} prices={prices} onApply={vi.fn()} />);
    const input = screen.getByLabelText("Целевая маржа, %");
    fireEvent.change(input, { target: { value: "95" } });
    expect(screen.getByRole("alert")).toHaveTextContent("от 0 до 80");
    expect(screen.getByRole("button", { name: "Рассчитать рекомендации" })).toBeDisabled();
    fireEvent.change(input, { target: { value: "25" } });
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать рекомендации" }));
    expect(await screen.findByText(/Не удалось рассчитать/)).toBeInTheDocument();
    expect(window.localStorage.getItem("mh.priceAdvisor.targetMargin")).toBe("25");
    expect(String((fetchMock.mock.calls[0] as unknown[])[0])).toContain("target_margin=25");
  });

  it("ignores a stale response when a newer request was started", async () => {
    const resolvers: Array<(value: unknown) => void> = [];
    vi.stubGlobal("fetch", vi.fn(() => new Promise((resolve) => { resolvers.push(resolve); })));
    render(<PriceAdvisorPanel period={period} prices={prices} onApply={vi.fn()} />);
    fireEvent.click(screen.getByRole("button", { name: "Рассчитать рекомендации" }));
    await waitFor(() => expect(resolvers).toHaveLength(1));
    fireEvent.change(screen.getByLabelText("Целевая маржа, %"), { target: { value: "30" } });
    fireEvent.click(screen.getByRole("button", { name: /Рассчитать|Пересчитать/ }));
    await waitFor(() => expect(resolvers).toHaveLength(2));
    resolvers[1]!({ ok: true, status: 200, json: () => Promise.resolve(body([item({ vendor_code: "NEW-1" })])) });
    expect(await screen.findByText("NEW-1")).toBeInTheDocument();
    resolvers[0]!({ ok: true, status: 200, json: () => Promise.resolve(body([item({ vendor_code: "OLD-1" })])) });
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(screen.queryByText("OLD-1")).toBeNull();
  });

  it("disables calculation without WB prices", () => {
    render(<PriceAdvisorPanel period={period} prices={[]} onApply={vi.fn()} />);
    expect(screen.getByRole("button", { name: "Рассчитать рекомендации" })).toBeDisabled();
  });
});
