import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProductionWorkspace } from "./ProductionWorkspace";

vi.mock("./data-source", () => ({ API_BASE_URL: "/tenant-api", apiHeaders: (json: boolean) => ({ "x-tenant-id": "test-tenant", ...(json ? { "content-type": "application/json" } : {}) }) }));
const data = { wip: { status: { open_batches: 0, open_cost_rub: 0, remaining_units: 0, remaining_cost_rub: 0 }, batches: [] }, materials: [{ material_name: "Ткань", unit: "м", available_meters: 20 }], products: [], capacity: { capacity_known: false, pieces_per_day: 0, workdays: [0, 1], horizon_days: 14, fulfillment_lead_days: 4, emergency_cover_days: 7, expedited_fbo_lead_days: 3, fbs_lead_days: 0, note: "" }, calendar: [], undated_tasks: [] };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const fetchMock = vi.fn<typeof fetch>();
beforeEach(() => { fetchMock.mockReset(); fetchMock.mockImplementation(async () => json(data)); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("shows failure without invented stock and can retry", async () => {
  fetchMock.mockImplementationOnce(async () => json({ detail: "Временно недоступно" }, 503));
  render(<ProductionWorkspace />);
  expect(await screen.findByRole("alert")).toHaveTextContent("Временно недоступно");
  expect(screen.queryByText("Выдать сырьё в НЗП")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("Повторить загрузку"));
  await screen.findByText("Выдать сырьё в НЗП");
});

it("disables read-only writes but keeps refresh and calendar available", async () => {
  fetchMock.mockImplementation(async () => json({ ...data, read_only: true }));
  render(<ProductionWorkspace />);
  await screen.findByText(/Доступ только для просмотра/);
  expect(screen.getByLabelText("Расход сырья, м")).toBeDisabled();
  expect(screen.getByRole("button", { name: "Выдать в НЗП" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Сохранить мощность" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Обновить производство" })).not.toBeDisabled();
  expect(screen.getByLabelText("Начало календаря")).not.toBeDisabled();
  expect(screen.getByLabelText("Период")).not.toBeDisabled();
  fireEvent.submit(screen.getByRole("button", { name: "Выдать в НЗП" }).closest("form")!);
  expect(fetchMock.mock.calls.filter(([, init]) => init?.method !== "GET")).toHaveLength(0);
});

it("uses catalogue units for issue quantities and historical batch display", async () => {
  fetchMock.mockImplementation(async () => json({ ...data, materials: [...data.materials, { material_name: "Клей", unit: "кг", available_meters: 5 }], wip: { ...data.wip, batches: [{ id: 7, material_name: "Клей", blank_type: "Заготовка", unit: "кг", status: "open", issued_meters: 2.5, material_cost_rub: 250, reversed_at: null }] } }));
  render(<ProductionWorkspace />); await screen.findByText("Выдать сырьё в НЗП");
  fireEvent.change(screen.getByLabelText("Материал"), { target: { value: "Клей" } });
  expect(screen.getByLabelText("Расход сырья, кг")).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Клей · 5 кг" })).toBeInTheDocument();
  expect(screen.getByText(/Выдано 2.5 кг/)).toBeInTheDocument();
});

it("keeps request identity on network retry, blocks duplicate submits and refreshes real state", async () => {
  const changed = vi.fn(); render(<ProductionWorkspace onChanged={changed} />);
  await screen.findByText("Выдать сырьё в НЗП");
  fireEvent.change(screen.getByLabelText("Тип заготовки"), { target: { value: "Круг" } });
  fireEvent.change(screen.getByLabelText("Расход сырья, м"), { target: { value: "2.5" } });
  let reject!: (error: Error) => void;
  fetchMock.mockImplementationOnce(() => new Promise((_resolve, fail) => { reject = fail; }));
  const form = screen.getByRole("button", { name: "Выдать в НЗП" }).closest("form")!;
  fireEvent.submit(form); fireEvent.submit(form);
  const writes = () => fetchMock.mock.calls.filter(([, args]) => args?.method === "POST");
  expect(writes()).toHaveLength(1);
  const payload = JSON.parse(writes()[0][1]?.body as string);
  expect(payload.meters_used).toBe(2.5);
  reject(new Error("Связь прервана"));
  await screen.findByRole("alert");
  fireEvent.submit(form);
  await screen.findByText("Сырьё выдано в НЗП. Остаток и стоимость обновлены.");
  expect(JSON.parse(writes()[1][1]?.body as string).request_id).toBe(payload.request_id);
  expect(changed).toHaveBeenCalledTimes(1);
  expect(screen.getByLabelText("Расход сырья, м")).toHaveValue(null);
});

it("does not present a committed operation as a failed write when refreshing fails", async () => {
  render(<ProductionWorkspace />); await screen.findByText("Выдать сырьё в НЗП");
  fireEvent.change(screen.getByLabelText("Тип заготовки"), { target: { value: "Круг" } });
  fireEvent.change(screen.getByLabelText("Расход сырья, м"), { target: { value: "2" } });
  fetchMock.mockImplementationOnce(async () => json({ posted: 1 })).mockImplementationOnce(async () => json({}, 503));
  fireEvent.submit(screen.getByRole("button", { name: "Выдать в НЗП" }).closest("form")!);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Операция сохранена, но список не обновился"));
  expect(screen.getByLabelText("Расход сырья, м")).toHaveValue(null);
});
