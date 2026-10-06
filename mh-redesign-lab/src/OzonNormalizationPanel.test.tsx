import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { OzonNormalizationPanel } from "./OzonNormalizationPanel";

vi.mock("./data-source", () => ({ API_BASE_URL: "", apiHeaders: () => ({ "Content-Type": "application/json" }) }));
vi.mock("./selected-shop", () => ({ scopedFetch: (input: RequestInfo | URL, init?: RequestInit) => globalThis.fetch(input, init) }));
const row = { source_occurrence_key: "row-1", source_row_index: 0, accrual_date: "2026-09-01", category: "POSTING", eligible: false,
  observed_total_rub: "80.00", normalized_total_rub: null, quantity: null, known_cogs_rub: null, document_event_ids: [], blockers: ["Нужны документы"] };
const preview = { loaded: true, fingerprint: "b".repeat(64), source_fingerprint: "a".repeat(64), rows: [row], observed_total_rub: null,
  normalized_total_rub: null, result_after_fifo_rub: null, normalized_row_count: 0, excluded_row_count: 1, unknown_cost_units: 1, source_collection_complete: false };
const journal = { lines: [{ external_event_id: "sale-1", sku: "123", namespace: "main", kind: "sale", accrued_at: "2026-09-01T10:00:00+03:00", source_total_rub: "80.00", posting_ref: "posting-one" }] };
const response = (body: unknown, status = 200) => ({ ok: status < 400, status, json: async () => body }) as Response;
beforeEach(() => vi.stubGlobal("fetch", vi.fn(async (url) => response(String(url).includes("/normalization/preview") ? preview : journal))));
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("keeps absent money unknown and a viewer cannot confirm links", async () => {
  render(<OzonNormalizationPanel start="2026-09-01" end="2026-09-30" canWrite={false} />);
  await screen.findByText(/Сверено строк: 0 из 1/);
  expect(screen.getAllByText("не рассчитано").length).toBeGreaterThanOrEqual(3);
  expect(screen.getByText(/Полнота загрузки источника не подтверждена/)).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Подтвердить связь документов" })).not.toBeInTheDocument();
  expect(screen.queryByLabelText("Начисление Ozon")).not.toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls.every(call => (call[1] as RequestInit).method === "GET")).toBe(true);
});

it("displays confirmed zero exactly and never rounds large Decimal amounts", async () => {
  vi.mocked(fetch).mockImplementation(async url => response(String(url).includes("/normalization/preview") ? {
    ...preview, result_after_fifo_rub: "0.00", normalized_total_rub: "-99999999999999999.99",
  } : journal));
  render(<OzonNormalizationPanel start="2026-09-01" end="2026-09-30" canWrite={false} />);
  expect(await screen.findByText("0,00 ₽")).toBeInTheDocument();
  expect(screen.getByText("-99 999 999 999 999 999,99 ₽")).toBeInTheDocument();
});

async function chooseDocuments() {
  const source = await screen.findByLabelText("Начисление Ozon");
  fireEvent.change(source, { target: { value: "row-1" } });
  fireEvent.click(screen.getByLabelText(/sale-1 · SKU 123/));
  const submit = screen.getByRole("button", { name: "Подтвердить связь документов" });
  expect(submit).toBeDisabled();
  fireEvent.click(screen.getByLabelText("Я проверил начисление и выбранные документы"));
  return submit;
}

it("posts only explicit source/documents with the reviewed fingerprint then reloads", async () => {
  vi.mocked(fetch).mockImplementation(async (url, init) => response(init?.method === "POST" ? { saved: true } : String(url).includes("/normalization/preview") ? preview : journal));
  render(<OzonNormalizationPanel start="2026-09-01" end="2026-09-30" canWrite />);
  fireEvent.click(await chooseDocuments());
  await screen.findByText(/Связь сохранена/);
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.filter(call => call[1]?.method === "GET")).toHaveLength(4));
  const post = vi.mocked(fetch).mock.calls.find(call => call[1]?.method === "POST")!;
  expect(JSON.parse(post[1]!.body as string)).toEqual({ start_date: "2026-09-01", end_date: "2026-09-30", namespace: "main",
    expected_fingerprint: "b".repeat(64), source_occurrence_key: "row-1", document_event_ids: ["sale-1"] });
  expect(screen.getByLabelText("Начисление Ozon")).toHaveValue("");
});

it("a stale-source conflict reloads and requires a new explicit choice", async () => {
  vi.mocked(fetch).mockImplementation(async (url, init) => response(init?.method === "POST" ? { detail: "Источник изменился" } : String(url).includes("/normalization/preview") ? preview : journal, init?.method === "POST" ? 409 : 200));
  render(<OzonNormalizationPanel start="2026-09-01" end="2026-09-30" canWrite />);
  fireEvent.click(await chooseDocuments());
  await screen.findByText(/Данные изменились/);
  await waitFor(() => expect(screen.getByLabelText("Начисление Ozon")).toHaveValue(""));
  expect(screen.getByRole("alert")).toHaveTextContent("Источник изменился");
  expect(screen.queryByRole("button", { name: "Подтвердить связь документов" })).not.toBeInTheDocument();
});

it("late previous-period responses cannot overwrite the new period and are aborted", async () => {
  let resolveOld!: (value: Response) => void;
  const pending = new Promise<Response>(resolve => { resolveOld = resolve; });
  vi.mocked(fetch).mockImplementation(async url => String(url).includes("start_date=2026-09-01") && String(url).includes("/normalization/preview") ? pending : response(String(url).includes("/normalization/preview") ? { ...preview, normalized_total_rub: "99.00" } : journal));
  const view = render(<OzonNormalizationPanel start="2026-09-01" end="2026-09-30" canWrite={false} />);
  const oldSignal = vi.mocked(fetch).mock.calls[0][1]!.signal!;
  view.rerender(<OzonNormalizationPanel start="2026-09-02" end="2026-09-30" canWrite={false} />);
  expect(await screen.findByText("99,00 ₽")).toBeInTheDocument();
  expect(oldSignal.aborted).toBe(true);
  await act(async () => resolveOld(response({ ...preview, normalized_total_rub: "777.00" })));
  expect(screen.queryByText("777,00 ₽")).not.toBeInTheDocument();
});

it("missing source has a useful empty state without a fabricated profit", async () => {
  vi.mocked(fetch).mockImplementation(async url => response(String(url).includes("/normalization/preview") ? { loaded: false, rows: [], result_after_fifo_rub: null } : { lines: [] }));
  render(<OzonNormalizationPanel start="2026-09-01" end="2026-09-30" canWrite />);
  expect(await screen.findByText(/Сначала загрузите данные Ozon/)).toBeInTheDocument();
  expect(screen.queryByText("0,00 ₽")).not.toBeInTheDocument();
});

it("namespace is applied explicitly instead of issuing requests on every keystroke", async () => {
  render(<OzonNormalizationPanel start="2026-09-01" end="2026-09-30" canWrite />);
  await screen.findByLabelText("Начисление Ozon");
  fireEvent.change(screen.getByLabelText("Реестр документов Ozon"), { target: { value: "second" } });
  expect(vi.mocked(fetch)).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "Применить реестр" }));
  await waitFor(() => expect(vi.mocked(fetch)).toHaveBeenCalledTimes(4));
  expect(String(vi.mocked(fetch).mock.calls[2][0])).toContain("namespace=second");
});

it("unmount aborts both source and documentary reads", () => {
  vi.mocked(fetch).mockImplementation(() => new Promise(() => {}));
  const view = render(<OzonNormalizationPanel start="2026-09-01" end="2026-09-30" canWrite />);
  const signals = vi.mocked(fetch).mock.calls.map(call => call[1]!.signal!);
  view.unmount();
  expect(signals).toHaveLength(2);
  expect(signals.every(signal => signal.aborted)).toBe(true);
});

it("bounds table rendering with explicit show more without hiding totals", async () => {
  vi.mocked(fetch).mockImplementation(async url => response(String(url).includes("/normalization/preview") ? {
    ...preview, rows: Array.from({ length: 51 }, (_, index) => ({ ...row, source_occurrence_key: `row-${index}`, source_row_index: index })),
    normalized_total_rub: "1234.00",
  } : journal));
  render(<OzonNormalizationPanel start="2026-09-01" end="2026-09-30" canWrite />);
  await screen.findByText(/Показано 50 из 51/);
  expect(screen.getAllByRole("row")).toHaveLength(51);
  expect(screen.getByText("1 234,00 ₽")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Показать ещё начисления" }));
  expect(screen.getAllByRole("row")).toHaveLength(52);
});
