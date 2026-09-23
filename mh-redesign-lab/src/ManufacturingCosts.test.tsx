import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ManufacturingCosts, manufacturingNumber, type ManufacturingCostsData } from "./ManufacturingCosts";

vi.mock("./data-source", () => ({ API_BASE_URL: "", apiHeaders: (json: boolean) => ({ "x-tenant-id": "test-tenant", ...(json ? { "content-type": "application/json" } : {}) }) }));
const data: ManufacturingCostsData = {
  materials: [{ material_name: "Ткань", unit: "м²" }], material_layers: [{ id: 5, material_name: "Ткань", unit: "м²", remaining_quantity: 10, unit_cost_rub: 50.5 }],
  products: [{ nm_id: 123, product_name: "Салфетки" }],
  boms: [{ id: 1, nm_id: 123, version: 1 }], equipment: [], piecework: [], defects: [], releases: [],
};
const json = (body: unknown, status = 200) => new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
const fetchMock = vi.fn<typeof fetch>();
beforeEach(() => { fetchMock.mockReset(); fetchMock.mockImplementation(async () => json(data)); vi.stubGlobal("fetch", fetchMock); });
afterEach(() => { cleanup(); vi.unstubAllGlobals(); });
async function open() { render(<ManufacturingCosts />); await screen.findByText("Состав изделия — новая версия"); }
function writes() { return fetchMock.mock.calls.filter(([, init]) => init?.method === "POST"); }
async function fillLabor() {
  await userEvent.type(screen.getByLabelText("Код задания для работы"), "job-1");
  await userEvent.selectOptions(screen.getByLabelText("Товар для сдельной работы"), "123");
  await userEvent.type(screen.getByLabelText("Название выполненной операции"), "Пошив");
  await userEvent.type(screen.getByLabelText("Исполнитель"), "Мария");
  await userEvent.type(screen.getByLabelText("Объём выполненной работы, ед."), "3");
  await userEvent.type(screen.getByLabelText("Сдельная расценка, ₽/ед."), "125,50");
}

describe("manufacturing cost input", () => {
  it("preserves cents and rejects silent rounding, scientific notation and fractional released units", () => {
    expect(manufacturingNumber("125,50", "Цена", { money: true })).toBe(125.5);
    expect(manufacturingNumber("0,125", "Материал", { positive: true })).toBe(0.125);
    for (const input of ["1.005", "1e3", "-1", "", "Infinity"]) expect(() => manufacturingNumber(input, "Цена", { money: true })).toThrow();
    expect(() => manufacturingNumber("1.5", "Выпуск", { integer: true })).toThrow();
  });
  it("uses catalogue material units and submits a version with decimal costs", async () => {
    await open();
    await userEvent.selectOptions(screen.getByLabelText("Товар для состава изделия"), "123");
    await userEvent.selectOptions(screen.getByLabelText("Материал 1"), "Ткань");
    await userEvent.clear(screen.getByLabelText("Расход материала 1, м²"));
    await userEvent.type(screen.getByLabelText("Расход материала 1, м²"), "0,25");
    await userEvent.clear(screen.getByLabelText("Упаковка, ₽/ед."));
    await userEvent.type(screen.getByLabelText("Упаковка, ₽/ед."), "12,50");
    await userEvent.click(screen.getByRole("button", { name: "Сохранить новую версию" }));
    await screen.findByText("Новая версия состава изделия сохранена.");
    expect(writes()[0][0]).toBe("/api/v1/manufacturing-costs/boms");
    expect(JSON.parse(writes()[0][1]?.body as string)).toMatchObject({ nm_id: 123, packaging_per_unit: 12.5, materials: [{ material_name: "Ткань", unit: "м²", quantity_per_unit: 0.25 }] });
  });
  it("keeps failed labor input and idempotency key for retry and prevents concurrent submits", async () => {
    await open(); await fillLabor();
    let rejectWrite!: (reason: Error) => void;
    fetchMock.mockImplementationOnce(() => new Promise((_resolve, reject) => { rejectWrite = reject; }));
    const button = screen.getByRole("button", { name: "Записать сдельную работу" });
    fireEvent.submit(button.closest("form")!); fireEvent.submit(button.closest("form")!);
    expect(writes()).toHaveLength(1); expect(button).toBeDisabled();
    const first = JSON.parse(writes()[0][1]?.body as string);
    expect(first.rate_per_unit).toBe(125.5);
    rejectWrite(new Error("Связь прервана"));
    await screen.findByRole("alert");
    expect(screen.getByLabelText("Сдельная расценка, ₽/ед.")).toHaveValue("125,50");
    await userEvent.click(button);
    await screen.findByText("Сдельная работа сохранена и будет включена в выпуск этого задания.");
    expect(JSON.parse(writes()[1][1]?.body as string).event_key).toBe(first.event_key);
  });
  it("rejects material scrap above available stock before calling backend", async () => {
    await open(); await userEvent.selectOptions(screen.getByLabelText("Партия сырья для списания"), "5");
    await userEvent.type(screen.getByLabelText("Количество брака, м²"), "10,1");
    await userEvent.type(screen.getByLabelText("Причина брака"), "Повреждение");
    await userEvent.click(screen.getByRole("button", { name: "Списать брак" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("превышает остаток"); expect(writes()).toHaveLength(0);
  });
  it("distinguishes committed write from refresh failure to prevent duplicate entry", async () => {
    await open(); await fillLabor();
    fetchMock.mockImplementationOnce(async () => json({ id: 1 })).mockImplementationOnce(async () => json({ detail: "Недоступно" }, 503));
    await userEvent.click(screen.getByRole("button", { name: "Записать сдельную работу" }));
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Запись сохранена, но список не обновился"));
    expect(screen.getByLabelText("Объём выполненной работы, ед.")).toHaveValue(""); expect(writes()).toHaveLength(1);
  });
  it("shows an unavailable backend without fabricated records and allows retry", async () => {
    fetchMock.mockImplementationOnce(async () => json({}, 404)); render(<ManufacturingCosts />);
    expect(await screen.findByRole("alert")).toHaveTextContent("пока недоступен");
    expect(screen.queryByText("Состав изделия — новая версия")).not.toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "Обновить данные" }));
    await screen.findByText("Состав изделия — новая версия");
  });
  it("allows a viewer to refresh and inspect the journal but never submit a write", async () => {
    render(<ManufacturingCosts canEdit={false} />);
    await screen.findByText("Состав изделия — новая версия");
    expect(screen.getByRole("button", { name: "Записать сдельную работу" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Обновить данные" })).toBeEnabled();
    fireEvent.submit(screen.getByRole("button", { name: "Списать брак" }).closest("form")!);
    expect(writes()).toHaveLength(0);
  });
  it("requires a matching BOM and sends integer quantity and job key for a release", async () => {
    const onChanged = vi.fn(); render(<ManufacturingCosts onChanged={onChanged} />);
    await screen.findByText("Состав изделия — новая версия");
    await userEvent.type(screen.getByLabelText("Код задания выпуска"), "job-2");
    await userEvent.selectOptions(screen.getByLabelText("Товар для выпуска"), "123");
    await userEvent.selectOptions(screen.getByLabelText("Версия состава для выпуска"), "1");
    await userEvent.type(screen.getByLabelText("Количество выпуска, целые ед."), "1,5");
    await userEvent.click(screen.getByRole("button", { name: "Провести выпуск" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("целое положительное число");
    expect(writes()).toHaveLength(0);
    await userEvent.clear(screen.getByLabelText("Количество выпуска, целые ед."));
    await userEvent.type(screen.getByLabelText("Количество выпуска, целые ед."), "3");
    await userEvent.click(screen.getByRole("button", { name: "Провести выпуск" }));
    await screen.findByText("Выпуск проведён: материалы и производственные затраты учтены.");
    expect(JSON.parse(writes()[0][1]?.body as string)).toMatchObject({ nm_id: 123, quantity: 3, bom_version_id: 1, source_key: "job-2" });
    expect(onChanged).toHaveBeenCalledOnce();
  });
  it("applies server permissions to each operation separately", async () => {
    fetchMock.mockImplementation(async () => json({ ...data, allowed_actions: ["piecework"] }));
    await open();
    expect(screen.getByRole("button", { name: "Записать сдельную работу" })).toBeEnabled();
    for (const name of ["Сохранить новую версию", "Добавить оборудование", "Списать брак", "Провести выпуск"]) {
      expect(screen.getByRole("button", { name })).toBeDisabled();
      fireEvent.submit(screen.getByRole("button", { name }).closest("form")!);
    }
    expect(writes()).toHaveLength(0);
    expect(screen.getByText(/Финансовый отчёт пока использует себестоимость из настроек товара/)).toBeVisible();
  });
  it("shows a used-batch reversal error and preserves the journal record", async () => {
    const withRelease = { ...data, allowed_actions: ["reverse"], releases: [{ id: 1, movement_id: 22, source_key: "job-used", status: "applied", quantity: 3, total_cost_rub: 458.25 }] };
    fetchMock.mockImplementation(async () => json(withRelease)); await open();
    fireEvent.click(screen.getByText(/job-used · 3 ед./));
    fetchMock.mockImplementationOnce(async () => json({ detail: "Партия уже использована" }, 422));
    await userEvent.click(screen.getByRole("button", { name: "Отменить выпуск job-used" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Партия уже использована");
    expect(writes()[0][0]).toBe("/api/v1/manufacturing-costs/movements/22/reverse");
    expect(screen.queryByText(/Выпуск отменён\./)).not.toBeInTheDocument();
  });
  it("refreshes a reversed defect and prevents reversing it again", async () => {
    const defect = { id: 3, movement_id: 25, material_name: "Ткань", quantity: 1.5, unit: "м²", cost_rub: 15.75, reason: "Повреждение", status: "applied" };
    fetchMock.mockImplementation(async () => json({ ...data, allowed_actions: ["reverse"], defects: [defect] })); await open();
    fetchMock.mockImplementationOnce(async () => json({ ok: true })).mockImplementationOnce(async () => json({ ...data, allowed_actions: ["reverse"], defects: [{ ...defect, status: "reversed" }] }));
    await userEvent.click(screen.getByRole("button", { name: "Отменить списание брака #3" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Отменить списание брака #3" })).toBeDisabled());
    expect(screen.getByText(/Списание брака отменено: сырьё/)).toBeVisible();
    expect(writes()).toHaveLength(1);
  });
});
