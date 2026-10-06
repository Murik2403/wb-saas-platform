import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { OzonMonthlyPanel } from "./OzonMonthlyPanel";
vi.mock("./data-source", () => ({ API_BASE_URL: "", apiHeaders: () => ({ "content-type": "application/json" }) }));
vi.mock("./selected-shop", () => ({ scopedFetch: (...args: Parameters<typeof fetch>) => globalThis.fetch(...args) }));
beforeEach(() => vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true, json: async () => ({ items: [{ month: "2026-09", document_lines: 1, revenue_rub: "0.00", net_document_total_rub: "0.00", historical_cogs_rub: null, result_after_fifo_rub: null, unknown_cost_units: 1 }], blockers: [], note: "Документальный результат" }) })));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
it("distinguishes known zero from missing FIFO and offers all six regimes", async () => {
  render(<OzonMonthlyPanel start="2026-09-01" end="2026-09-30" canWrite />);
  expect(await screen.findByText("2026-09")).toBeInTheDocument();
  expect(screen.getAllByText("Нет данных")).toHaveLength(2);
  expect(screen.getAllByText("0,00 ₽")).toHaveLength(2);
  expect(screen.getByLabelText("Режим").querySelectorAll("option")).toHaveLength(6);
});
it("never fills tax income from accruals and sends only explicit tax inputs", async () => {
  render(<OzonMonthlyPanel start="2026-09-01" end="2026-09-30" canWrite />);
  await screen.findByText("2026-09");
  fireEvent.change(screen.getByLabelText("Получено денег, ₽"), { target: { value: "100,00" } });
  vi.mocked(fetch).mockResolvedValueOnce({ ok: true, json: async () => ({ estimate: { status: "incomplete", missing: ["refunds"], estimate: null }, note: "" }) } as Response);
  fireEvent.click(screen.getByText("Рассчитать оценку налога"));
  await screen.findByText("Заполните: Возвращено покупателям");
  const body = JSON.parse(String(vi.mocked(fetch).mock.calls.at(-1)?.[1]?.body));
  expect(body.options.received).toBe("100.00"); expect(body.options.refunds).toBeUndefined();
  fireEvent.change(screen.getByLabelText("Получено денег, ₽"), { target: { value: "200" } });
  await waitFor(() => expect(screen.queryByText("Заполните: Возвращено покупателям")).not.toBeInTheDocument());
});
it("viewer does not submit tax calculations", async () => {
  render(<OzonMonthlyPanel start="2026-09-01" end="2026-09-30" canWrite={false} />);
  expect(screen.getByText("Рассчитать оценку налога")).toBeDisabled();
  await screen.findByText("2026-09");
  expect(vi.mocked(fetch).mock.calls.every(call => !call[1]?.method || call[1]?.method === "GET")).toBe(true);
});
