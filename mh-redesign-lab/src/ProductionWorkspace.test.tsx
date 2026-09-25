import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProductionWorkspace } from "./ProductionWorkspace";

vi.mock("./data-source", () => ({ API_BASE_URL: "/tenant-api", apiHeaders: (json: boolean) => ({ "x-tenant-id": "test-tenant", ...(json ? { "content-type": "application/json" } : {}) }) }));
const data = { wip: { status: { open_batches: 0, open_cost_rub: 0, remaining_units: 0, remaining_cost_rub: 0 }, batches: [] }, materials: [{ material_name: "Ткань", unit: "м", available_meters: 20 }], products: [{ nm_id: 101, chrt_id: 0, variant_label: "Базовый товар", supplier_article: "QA", product_name: "QA", pack_size: 2 }], capacity: { capacity_known: false, pieces_per_day: 0, workdays: [0, 1], horizon_days: 14, fulfillment_lead_days: 4, emergency_cover_days: 7, expedited_fbo_lead_days: 3, fbs_lead_days: 0, note: "" }, calendar: [], undated_tasks: [] };
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

it.each([["м", "Ткань"], ["кг", "Клей"], ["л", "Масло"], ["шт", "Фурнитура"]])("uses the catalogue unit %s for issue quantities without changing the API contract", async (unit, name) => {
  const materials = [
    { material_name: "Ткань", unit: "м", available_meters: 20 },
    { material_name: "Клей", unit: "кг", available_meters: 5 },
    { material_name: "Масло", unit: "л", available_meters: 3 },
    { material_name: "Фурнитура", unit: "шт", available_meters: 12 },
  ];
  fetchMock.mockImplementation(async () => json({ ...data, materials }));
  render(<ProductionWorkspace />); await screen.findByText("Выдать сырьё в НЗП");
  fireEvent.change(screen.getByLabelText("Материал"), { target: { value: name } });
  expect(screen.getByLabelText(`Расход сырья, ${unit}`)).toBeInTheDocument();
  expect(screen.getByRole("option", { name: `${name} · ${materials.find((m) => m.material_name === name)!.available_meters} ${unit}` })).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(`Расход сырья, ${unit}`), { target: { value: "2.5" } });
  fireEvent.submit(screen.getByRole("button", { name: "Выдать в НЗП" }).closest("form")!);
  await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
  const payload = JSON.parse(fetchMock.mock.calls.find(([, init]) => init?.method === "POST")![1]?.body as string);
  expect(payload).toMatchObject({ material_name: name, meters_used: 2.5 });
});

it("does not invent metres when the material unit is missing", async () => {
  fetchMock.mockImplementation(async () => json({ ...data, materials: [{ material_name: "Сырьё без единицы", unit: "", available_meters: 5 }] }));
  render(<ProductionWorkspace />); await screen.findByText("Выдать сырьё в НЗП");
  expect(screen.getByLabelText("Расход сырья, ед.")).toBeInTheDocument();
  expect(screen.getByRole("option", { name: "Сырьё без единицы · 5 ед." })).toBeInTheDocument();
});

it("submits the selected product and exact variant for WIP issue and packaging", async () => {
  const products = [
    { nm_id: 101, chrt_id: 501, variant_label: "S", supplier_article: "QA", product_name: "QA", pack_size: 2 },
    { nm_id: 101, chrt_id: 502, variant_label: "M", supplier_article: "QA", product_name: "QA", pack_size: 2 },
  ];
  fetchMock.mockImplementation(async () => json({ ...data, products }));
  render(<ProductionWorkspace />); await screen.findByText("Выдать сырьё в НЗП");
  const issueForm = screen.getByRole("button", { name: "Выдать в НЗП" }).closest("form")!;
  fireEvent.change(screen.getAllByLabelText("Товар и вариант")[0], { target: { value: "101:501" } });
  fireEvent.change(screen.getByLabelText("Тип заготовки"), { target: { value: "Круг" } });
  fireEvent.change(screen.getByLabelText("Расход сырья, м"), { target: { value: "2" } });
  fireEvent.submit(issueForm);
  await screen.findByText("Сырьё выдано в НЗП. Остаток и стоимость обновлены.");
  const issueBody = JSON.parse(fetchMock.mock.calls.find(([, init]) => init?.method === "POST")![1]?.body as string);
  expect(issueBody).toMatchObject({ nm_id: 101, chrt_id: 501 });
  const packageSelect = screen.getAllByLabelText("Товар и вариант")[1] as HTMLSelectElement;
  fireEvent.change(packageSelect, { target: { value: "101:502" } });
  fireEvent.change(screen.getByLabelText("Количество комплектов"), { target: { value: "1" } });
  fireEvent.submit(screen.getByRole("button", { name: "Собрать из заготовок" }).closest("form")!);
  await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(2));
  const packageBody = JSON.parse(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")[1][1]?.body as string);
  expect(packageBody).toMatchObject({ nm_id: 101, chrt_id: 502 });
});

it("labels legacy WIP batches as unassigned and only offers catalog product variants", async () => {
  const legacyBatch = { id: 7, nm_id: 0, chrt_id: 0, product_label: "", unit: "м", material_name: "Ткань", blank_type: "Круг", status: "completed", issued_meters: 2, material_cost_rub: 20, produced_units: 3, scrap_units: 0, remaining_units: 3, unit_cost_rub: 6.67, completion_movement_id: 2, reversed_at: null };
  const products = [
    { nm_id: 101, chrt_id: 501, variant_label: "S", supplier_article: "QA", product_name: "QA", pack_size: 2 },
    { nm_id: 101, chrt_id: 502, variant_label: "M", supplier_article: "QA", product_name: "QA", pack_size: 2 },
  ];
  fetchMock.mockImplementation(async () => json({ ...data, products, wip: { ...data.wip, batches: [legacyBatch] } }));
  render(<ProductionWorkspace />); await screen.findByText("Выдать сырьё в НЗП");
  expect(screen.getByText("Старые данные без привязки к товару и варианту")).toBeInTheDocument();
  const options = Array.from((screen.getAllByLabelText("Товар и вариант")[0] as HTMLSelectElement).options).map((option) => option.value);
  expect(options).toEqual(["101:501", "101:502"]);
  expect(options).not.toContain("0:0");
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
