import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mocks = vi.hoisted(() => ({ preview: vi.fn(), confirm: vi.fn() }));
vi.mock("./data-source", () => ({
  previewSupplyPlan: mocks.preview,
  confirmSupplyPlan: mocks.confirm,
  SupplyPlanHttpError: class SupplyPlanHttpError extends Error { constructor(readonly status: number, readonly code: string | null, message: string) { super(message); } },
}));

import { SupplyPlanning } from "./SupplyPlanning";

const line = {
  material_key: "fabric-a", material_name: "Ткань A", unit: "м", required_base_qty: 6,
  physical_available_base_qty: 0, open_order_covered_base_qty: 0, shortage_base_qty: 6,
  pack_size_base_qty: 10, suggested_purchase_qty: 1, due_date: "2026-10-10", tracking_mode: "packaged" as const,
  fallback_to_base_bom: false,
  supplier_candidates: [{ id: 7, name: "Поставщик A", currency: "CNY", lead_time_days: 4 }],
  source_refs: [{ task_key: "job:part", nm_id: 10, chrt_id: 0, units: 3, bom_version: 2, bom_version_id: 5 }],
};
const preview = {
  start_date: "2026-09-25", end_date: "2026-10-09", preview_fingerprint: "a".repeat(64),
  lines: [line], blockers: [], warnings: [], variant_demands: [],
};
const json = (body: unknown) => body;

beforeEach(() => {
  mocks.preview.mockReset().mockResolvedValue(json(preview));
  mocks.confirm.mockReset().mockResolvedValue({ confirmation_key: "key", order_ids: [1], order_numbers: ["ЗАК-1"], replayed: false });
  vi.stubGlobal("confirm", vi.fn(() => true));
  vi.stubGlobal("crypto", { randomUUID: vi.fn(() => "00000000-0000-4000-8000-000000000099") });
});
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

async function calculate(readOnly = false) {
  render(<SupplyPlanning readOnly={readOnly} onRefresh={vi.fn().mockResolvedValue(undefined)} />);
  await userEvent.click(screen.getByRole("button", { name: "Рассчитать потребность" }));
  await screen.findByText("Ткань A");
}

describe("SupplyPlanning", () => {
  it("keeps quantity when supplier and ETA are edited, and waits for explicit confirmation", async () => {
    await calculate();
    const quantity = screen.getByLabelText("Количество заказа Ткань A");
    fireEvent.change(quantity, { target: { value: "2" } });
    await userEvent.selectOptions(screen.getByLabelText("Поставщик Ткань A"), "7");
    expect(quantity).toHaveValue(2);
    const eta = screen.getByLabelText("Ожидаемая дата поставки Ткань A");
    expect(eta).toHaveValue();
    fireEvent.change(eta, { target: { value: "2026-10-08" } });
    expect(mocks.confirm).not.toHaveBeenCalled();
    await userEvent.click(screen.getByRole("button", { name: "Создать внутреннюю заявку" }));
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(1));
    expect(window.confirm).toHaveBeenCalledWith(expect.stringContaining("1 внутренних заявок для 1 позиций"));
    expect(mocks.confirm.mock.calls[0][0].edits[0]).toMatchObject({ quantity: 2, supplier_id: 7, expected_date: "2026-10-08" });
    expect(await screen.findByRole("status")).toHaveTextContent("Заказ поставщику не отправлялся");
  });

  it("retries an uncertain request with the same idempotency key and exact payload", async () => {
    mocks.confirm.mockRejectedValueOnce(new Error("network timeout"));
    await calculate();
    await userEvent.selectOptions(screen.getByLabelText("Поставщик Ткань A"), "7");
    await userEvent.click(screen.getByRole("button", { name: "Создать внутреннюю заявку" }));
    await screen.findByRole("alert");
    const retry = screen.getByRole("button", { name: "Повторить подтверждение" });
    expect(screen.getByLabelText("Количество заказа Ткань A")).toBeDisabled();
    const first = structuredClone(mocks.confirm.mock.calls[0][0]);
    await userEvent.click(retry);
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(2));
    expect(mocks.confirm.mock.calls[1][0]).toEqual(first);
    expect(mocks.confirm.mock.calls[1][0].confirmation_key).toBe(first.confirmation_key);
    expect(await screen.findByRole("status")).toHaveTextContent("ЗАК-1");
  });

  it("does not dispatch concurrent confirmations from a rapid double click", async () => {
    await calculate();
    await userEvent.selectOptions(screen.getByLabelText("Поставщик Ткань A"), "7");
    const button = screen.getByRole("button", { name: "Создать внутреннюю заявку" });
    fireEvent.click(button); fireEvent.click(button);
    await waitFor(() => expect(mocks.confirm).toHaveBeenCalledTimes(1));
  });

  it("keeps ambiguous task keys containing colons intact in explicit allocations", async () => {
    mocks.preview.mockReset()
      .mockResolvedValueOnce({ ...preview, lines: [], blockers: [{ code: "ambiguous_variant_demand", message: "Распределите план", task_key: "job:part", units: 5, chrt_ids: [101, 102] }], variant_demands: [{ task_key: "job:part", nm_id: 10, units: 5, chrt_ids: [101, 102], task_date: "2026-10-10" }] })
      .mockResolvedValueOnce({ ...preview, lines: [], blockers: [], variant_demands: [] });
    render(<SupplyPlanning readOnly={false} onRefresh={vi.fn()} />);
    await userEvent.click(screen.getByRole("button", { name: "Рассчитать потребность" }));
    const first = await screen.findByLabelText("Вариант 101");
    fireEvent.change(first, { target: { value: "2" } });
    fireEvent.change(screen.getByLabelText("Вариант 102"), { target: { value: "3" } });
    await userEvent.click(screen.getByRole("button", { name: "Пересчитать с распределением" }));
    await waitFor(() => expect(mocks.preview).toHaveBeenCalledTimes(2));
    expect(mocks.preview.mock.calls[1][0].variant_allocations).toEqual([
      { task_key: "job:part", chrt_id: 101, units: 2 }, { task_key: "job:part", chrt_id: 102, units: 3 },
    ]);
  });

  it("does not expose confirmation to read-only roles", async () => {
    await calculate(true);
    await userEvent.selectOptions(screen.getByLabelText("Поставщик Ткань A"), "7");
    expect(screen.getByRole("button", { name: "Создать внутреннюю заявку" })).toBeDisabled();
    expect(mocks.confirm).not.toHaveBeenCalled();
  });

  it("marks a period-edited preview stale and prevents confirmation", async () => {
    await calculate();
    const endDate = screen.getByLabelText("По дату");
    fireEvent.change(endDate, { target: { value: "2026-10-15" } });
    await userEvent.selectOptions(screen.getByLabelText("Поставщик Ткань A"), "7");
    expect(await screen.findByRole("status")).toHaveTextContent("Предпросмотр устарел");
    expect(screen.getByRole("button", { name: "Создать внутреннюю заявку" })).toBeDisabled();
    expect(mocks.confirm).not.toHaveBeenCalled();
  });

  it.each([
    ["ambiguous_variant_demand", "Распределите варианты"],
    ["incomplete_variant_allocation", "Укажите весь объём"],
    ["unknown_inventory_balance", "Подтвердите остаток"],
    ["missing_bom", "Нет спецификации"],
    ["unit_mismatch", "Сверьте единицы"],
    ["invalid_pack_size", "Исправьте размер рулона"],
  ])("blocks confirmation while preview contains %s", async (code, message) => {
    mocks.preview.mockResolvedValue({ ...preview, blockers: [{ code, message }] });
    await calculate();
    expect(await screen.findByRole("alert")).toHaveTextContent(message);
    expect(screen.getByRole("button", { name: "Создать внутреннюю заявку" })).toBeDisabled();
    expect(mocks.confirm).not.toHaveBeenCalled();
  });

  it("blocks fractional quantities for packaged material even when the suggested amount is fractional", async () => {
    mocks.preview.mockResolvedValue({ ...preview, lines: [{ ...line, suggested_purchase_qty: 0.5 }] });
    await calculate();
    const quantity = screen.getByLabelText("Количество заказа Ткань A");
    fireEvent.change(quantity, { target: { value: "1.5" } });
    await userEvent.selectOptions(screen.getByLabelText("Поставщик Ткань A"), "7");
    expect(screen.getByRole("button", { name: "Создать внутреннюю заявку" })).toBeDisabled();
    expect(mocks.confirm).not.toHaveBeenCalled();
  });
});
