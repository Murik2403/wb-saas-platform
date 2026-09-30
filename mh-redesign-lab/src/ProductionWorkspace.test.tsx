import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ProductionWorkspace } from "./ProductionWorkspace";

vi.mock("./data-source", () => ({ API_BASE_URL: "/tenant-api", apiHeaders: (json: boolean) => ({ "x-tenant-id": "test-tenant", ...(json ? { "content-type": "application/json" } : {}) }) }));
const data = { wip: { status: { open_batches: 0, open_cost_rub: 0, remaining_units: 0, remaining_cost_rub: 0 }, batches: [] }, materials: [{ material_name: "Ткань", unit: "м", available_meters: 20 }], products: [{ nm_id: 101, chrt_id: 0, variant_label: "Базовый товар", supplier_article: "QA", product_name: "QA", pack_size: 2, material_name: "Ткань", blank_type: "Круг" }], capacity: { capacity_known: false, pieces_per_day: 0, workdays: [0, 1], horizon_days: 14, fulfillment_lead_days: 4, emergency_cover_days: 7, expedited_fbo_lead_days: 3, fbs_lead_days: 0, note: "" }, calendar: [], undated_tasks: [] };
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const fetchMock = vi.fn<typeof fetch>();
beforeEach(() => { fetchMock.mockReset(); fetchMock.mockImplementation(async () => json(data)); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });

it("separates WIP and capacity without discarding a draft or reloading on a tab switch", async () => {
  const { rerender } = render(<ProductionWorkspace view="wip" />);
  const quantity = await screen.findByLabelText("Расход сырья, м");
  fireEvent.change(quantity, { target: { value: "2.5" } });
  expect(screen.getByRole("button", { name: "Выдать в НЗП" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Сохранить мощность" })).not.toBeInTheDocument();
  const requests = fetchMock.mock.calls.length;
  rerender(<ProductionWorkspace view="capacity" />);
  expect(screen.getByRole("button", { name: "Сохранить мощность" })).toBeVisible();
  expect(screen.queryByRole("button", { name: "Выдать в НЗП" })).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText("Штук в рабочий день"), { target: { value: "120" } });
  rerender(<ProductionWorkspace view="wip" />);
  expect(screen.getByLabelText("Расход сырья, м")).toHaveValue(2.5);
  rerender(<ProductionWorkspace view="capacity" />);
  expect(screen.getByLabelText("Штук в рабочий день")).toHaveValue(120);
  expect(fetchMock.mock.calls).toHaveLength(requests);
});

it("collapses empty calendar days but preserves work, unknown norms and overload", async () => {
  const day = { date: "2026-09-30", planned_pieces: 0, actual_pieces: 0, capacity_pieces: 100, overload_pieces: 0, missing_pack_size: false, tasks: [] };
  fetchMock.mockImplementation(async () => json({ ...data, calendar: [day, { ...day, date: "2026-10-01", planned_pieces: 20 }, { ...day, date: "2026-10-02", missing_pack_size: true }] }));
  render(<ProductionWorkspace view="capacity" />);
  await screen.findByRole("button", { name: "Показать дни без заданий (1)" });
  expect(screen.queryByText("2026-09-30")).not.toBeInTheDocument();
  expect(screen.getByText("2026-10-01")).toBeVisible();
  expect(screen.getByText("2026-10-02")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Показать дни без заданий (1)" }));
  expect(screen.getByText("2026-09-30")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Скрыть дни без заданий" }));
  expect(screen.queryByText("2026-09-30")).not.toBeInTheDocument();
});

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

it("uses the saved BOM material unit for issue quantities and historical batch display", async () => {
  fetchMock.mockImplementation(async () => json({ ...data, materials: [...data.materials, { material_name: "Клей", unit: "кг", available_meters: 5 }], products: [{ ...data.products[0], material_name: "Клей", blank_type: "Заготовка" }], wip: { ...data.wip, batches: [{ id: 7, material_name: "Клей", blank_type: "Заготовка", unit: "кг", status: "open", issued_meters: 2.5, material_cost_rub: 250, reversed_at: null }] } }));
  render(<ProductionWorkspace />); await screen.findByText("Выдать сырьё в НЗП");
  expect(screen.getByLabelText("Расход сырья, кг")).toBeInTheDocument();
  expect(screen.getByLabelText("Материал / цвет по норме")).toHaveValue("Клей");
  expect(screen.getByLabelText("Материал / цвет по норме")).toHaveAttribute("readonly");
  expect(screen.getByText(/Выдано 2.5 кг/)).toBeInTheDocument();
});

it("lets users select a configured product when the first product has incomplete norms", async () => {
  fetchMock.mockImplementation(async () => json({ ...data, products: [
    { ...data.products[0], material_name: "", blank_type: "" },
    { ...data.products[0], nm_id: 102, product_name: "Готовый к производству" },
  ] }));
  render(<ProductionWorkspace />);
  await screen.findByText("Выдать сырьё в НЗП");
  const selector = screen.getAllByLabelText("Товар и вариант")[0];
  expect(selector).not.toBeDisabled();
  expect(screen.getByRole("button", { name: "Выдать в НЗП" })).toBeDisabled();
  fireEvent.change(selector, { target: { value: "102:0" } });
  expect(screen.getByLabelText("Материал / цвет по норме")).toHaveValue("Ткань");
  expect(screen.getByRole("button", { name: "Выдать в НЗП" })).not.toBeDisabled();
});

it("posts the selected WB variant when packaging WIP blanks", async () => {
  fetchMock.mockImplementation(async () => json({ ...data, products: [
    { ...data.products[0], nm_id: 101, chrt_id: 501, variant_label: "S", product_name: "Футболка", supplier_article: "TEE" },
    { ...data.products[0], nm_id: 101, chrt_id: 502, variant_label: "M", product_name: "Футболка", supplier_article: "TEE" },
  ] }));
  render(<ProductionWorkspace />);
  await screen.findByText("Собрать готовые комплекты");
  const form = screen.getByRole("button", { name: "Собрать из заготовок" }).closest("form")!;
  fireEvent.change(screen.getAllByLabelText("Товар и вариант")[1], { target: { value: "101:502" } });
  fireEvent.change(screen.getByLabelText("Количество комплектов"), { target: { value: "3" } });
  fireEvent.submit(form);
  await waitFor(() => expect(fetchMock.mock.calls.some(([, init]) => init?.method === "POST")).toBe(true));
  const write = fetchMock.mock.calls.find(([, init]) => init?.method === "POST")!;
  expect(JSON.parse(write[1]?.body as string)).toMatchObject({ nm_id: 101, chrt_id: 502, qty_sets: 3 });
});

it("submits the selected product and exact variant for WIP issue and packaging", async () => {
  const products = [
    { ...data.products[0], nm_id: 101, chrt_id: 501, variant_label: "S", supplier_article: "TEE", product_name: "Футболка" },
    { ...data.products[0], nm_id: 101, chrt_id: 502, variant_label: "M", supplier_article: "TEE", product_name: "Футболка" },
  ];
  fetchMock.mockImplementation(async () => json({ ...data, products }));
  render(<ProductionWorkspace />);
  await screen.findByText("Выдать сырьё в НЗП");
  const selects = screen.getAllByLabelText("Товар и вариант");
  fireEvent.change(selects[0], { target: { value: "101:501" } });
  fireEvent.change(screen.getByLabelText("Расход сырья, м"), { target: { value: "2" } });
  fireEvent.submit(screen.getByRole("button", { name: "Выдать в НЗП" }).closest("form")!);
  await screen.findByText("Сырьё выдано в НЗП. Остаток и стоимость обновлены.");
  const issue = JSON.parse(fetchMock.mock.calls.find(([, init]) => init?.method === "POST")![1]?.body as string);
  expect(issue).toMatchObject({ nm_id: 101, chrt_id: 501 });
  expect(issue).toMatchObject({ material_name: "Ткань", blank_type: "Круг" });
  expect(screen.getByLabelText("Тип заготовки по норме")).toHaveAttribute("readonly");

  fireEvent.change(selects[1], { target: { value: "101:502" } });
  fireEvent.change(screen.getByLabelText("Количество комплектов"), { target: { value: "1" } });
  fireEvent.submit(screen.getByRole("button", { name: "Собрать из заготовок" }).closest("form")!);
  await waitFor(() => expect(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")).toHaveLength(2));
  const packageBody = JSON.parse(fetchMock.mock.calls.filter(([, init]) => init?.method === "POST")[1][1]?.body as string);
  expect(packageBody).toMatchObject({ nm_id: 101, chrt_id: 502 });
});

it("labels legacy WIP batches as unassigned and only offers catalog variants", async () => {
  const legacyBatch = { id: 7, nm_id: 0, chrt_id: 0, product_label: "", unit: "м", material_name: "Ткань", blank_type: "Круг", status: "completed", issued_meters: 2, material_cost_rub: 20, produced_units: 3, scrap_units: 0, remaining_units: 3, unit_cost_rub: 6.67, completion_movement_id: 2, reversed_at: null };
  const products = [
    { ...data.products[0], nm_id: 101, chrt_id: 501, variant_label: "S", supplier_article: "TEE", product_name: "Футболка" },
    { ...data.products[0], nm_id: 101, chrt_id: 502, variant_label: "M", supplier_article: "TEE", product_name: "Футболка" },
  ];
  fetchMock.mockImplementation(async () => json({ ...data, products, wip: { ...data.wip, batches: [legacyBatch] } }));
  render(<ProductionWorkspace />);
  await screen.findByText("Выдать сырьё в НЗП");
  expect(screen.getByText("Старые данные без привязки к товару и варианту")).toBeInTheDocument();
  const options = Array.from((screen.getAllByLabelText("Товар и вариант")[0] as HTMLSelectElement).options).map(option => option.value);
  expect(options).toEqual(["101:501", "101:502"]);
});

it("keeps request identity on network retry, blocks duplicate submits and refreshes real state", async () => {
  const changed = vi.fn(); render(<ProductionWorkspace onChanged={changed} />);
  await screen.findByText("Выдать сырьё в НЗП");
  fireEvent.change(screen.getByLabelText("Расход сырья, м"), { target: { value: "2.5" } });
  expect(screen.getByLabelText("Расход сырья, м")).toHaveValue(2.5);
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
  fireEvent.change(screen.getByLabelText("Расход сырья, м"), { target: { value: "2" } });
  fetchMock.mockImplementationOnce(async () => json({ posted: 1 })).mockImplementationOnce(async () => json({}, 503));
  fireEvent.submit(screen.getByRole("button", { name: "Выдать в НЗП" }).closest("form")!);
  await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Операция сохранена, но список не обновился"));
  expect(screen.getByLabelText("Расход сырья, м")).toHaveValue(null);
});
