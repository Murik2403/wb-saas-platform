import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { PriceUploadDetails } from "./PriceUploadDetails";

const item = { wb_upload_id: 77, goods_count: 2, status: "Цены обновлены", error: "", created_at: "2026-10-01 10:00", updated_at: "x" };
const detail = (over = {}) => ({ wb_upload_id: 77, actor: "acc-1", item_count: 2, error_count: 1, items: [
  { nm_id: 1, vendor_code: "A-1", old_price: 1000, old_discount: 5, new_price: 900, new_discount: 10, result_error: "" },
  { nm_id: 2, vendor_code: null, old_price: null, old_discount: null, new_price: 500, new_discount: 0, result_error: "price rejected" },
], ...over });
const mockFetch = (impl: () => Promise<unknown>) => vi.stubGlobal("fetch", vi.fn(impl));
const ok = (body: unknown) => Promise.resolve({ ok: true, status: 200, json: () => Promise.resolve(body) });

afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

describe("PriceUploadDetails", () => {
  it("loads lazily on first expand, shows rows and summary, collapses", async () => {
    mockFetch(() => ok(detail()));
    render(<PriceUploadDetails item={item} />);
    const button = screen.getByRole("button", { name: "Детали" });
    expect(button).toHaveAttribute("aria-expanded", "false");
    expect(fetch).not.toHaveBeenCalled();
    fireEvent.click(button);
    expect(await screen.findByText("A-1")).toBeInTheDocument();
    expect(screen.getByText(/2 SKU · 1 ошибок/)).toBeInTheDocument();
    expect(screen.getByText("1 000 ₽ / 5% / 950 ₽")).toBeInTheDocument();
    expect(screen.getByText("price rejected")).toBeInTheDocument();
    expect(button).toHaveAttribute("aria-expanded", "true");
    fireEvent.click(button);
    expect(screen.queryByText("A-1")).toBeNull();
    fireEvent.click(button);
    expect(await screen.findByText("A-1")).toBeInTheDocument();
    expect(fetch).toHaveBeenCalledTimes(1);
  });

  it("shows a message for old uploads without items", async () => {
    mockFetch(() => ok(detail({ items: [], item_count: 0, error_count: 0, actor: null })));
    render(<PriceUploadDetails item={item} />);
    fireEvent.click(screen.getByRole("button", { name: "Детали" }));
    expect(await screen.findByText("Детали этой загрузки не сохранились")).toBeInTheDocument();
  });

  it("shows an error and retries", async () => {
    let calls = 0;
    mockFetch(() => (++calls === 1 ? Promise.resolve({ ok: false, status: 500 }) : ok(detail())));
    render(<PriceUploadDetails item={item} />);
    fireEvent.click(screen.getByRole("button", { name: "Детали" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Не удалось загрузить детали");
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    await waitFor(() => expect(screen.getByText("A-1")).toBeInTheDocument());
  });
});
