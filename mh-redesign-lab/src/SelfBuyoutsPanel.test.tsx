import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { SelfBuyoutsPanel } from "./SelfBuyoutsPanel";
import { BuyoutError, createSelfBuyout, deleteSelfBuyout, getSelfBuyouts, parseRubToKop, updateSelfBuyout, type BuyoutReport } from "./self-buyouts-source";

vi.mock("./self-buyouts-source", async (importActual) => ({
  ...(await importActual<typeof import("./self-buyouts-source")>()),
  getSelfBuyouts: vi.fn(), createSelfBuyout: vi.fn(), updateSelfBuyout: vi.fn(), deleteSelfBuyout: vi.fn(),
}));

const zero = { records: 0, units: 0, paid_kop: 0, cashback_kop: 0, net_cost_kop: 0 };
const report: BuyoutReport = {
  start: "2026-08-12", end: "2026-09-10", group: "day", nm_id: null, previous_window: { start: "2026-07-13", end: "2026-08-11" },
  totals: { records: 1, units: 6, paid_kop: 290000, cashback_kop: 39000, net_cost_kop: 251000 }, previous_totals: { ...zero, units: 8, paid_kop: 100000 },
  deltas: {
    units: { value: 6, previous: 8, delta: -2, delta_pct: -25 },
    paid_kop: { value: 290000, previous: 100000, delta: 190000, delta_pct: 190 },
    cashback_kop: { value: 39000, previous: 0, delta: 39000, delta_pct: null },
    net_cost_kop: { value: 251000, previous: 100000, delta: 151000, delta_pct: 151 },
  },
  groups: [{ start: "2026-09-07", records: 1, units: 6, paid_kop: 290000, cashback_kop: 39000, net_cost_kop: 251000 }],
  records: [{ id: 5, day: "2026-09-07", nm_id: 11, vendor_code: "ART-11", units: 6, buyout_amount_kop: 290000, cashback_kop: 39000, net_cost_kop: 251000, format: "метка", note: "заметка", created_at: "", actor: null }],
  truncated: false,
};
const products = [{ nmId: "11", article: "ART-11", product: "Платье" }, { nmId: "12", article: "ART-12", product: "Брюки" }];
const period = { id: "30d" as const };
const flat = (text: string) => (_: string, el: Element | null) => !!el && el.children.length === 0 && (el.textContent ?? "").replace(/\s/g, " ") === text;

function open(role = "owner", confirm = vi.fn().mockResolvedValue(true)) {
  const view = render(<SelfBuyoutsPanel role={role} products={products} period={period} confirm={confirm} />);
  const details = view.container.querySelector("details")!;
  details.open = true; fireEvent(details, new Event("toggle"));
  return { ...view, confirm };
}

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(getSelfBuyouts).mockResolvedValue(report);
});

describe("parseRubToKop", () => {
  it("parses comma, dot and spaces without floats", () => {
    expect(parseRubToKop("1250,50")).toBe(125050);
    expect(parseRubToKop("1 250.5")).toBe(125050);
    expect(parseRubToKop("0,07")).toBe(7);
    expect(parseRubToKop("19.99")).toBe(1999);
    expect(parseRubToKop("100")).toBe(10000);
  });
  it("rejects negatives, garbage and sub-kopeck precision", () => {
    for (const bad of ["", "-5", "abc", "1,234", "1e3", "1,,5", "12 р", "+5", "NaN"]) expect(parseRubToKop(bad)).toBeNull();
  });
});

describe("SelfBuyoutsPanel", () => {
  it("fetches nothing until the section is opened", async () => {
    const view = render(<SelfBuyoutsPanel role="owner" products={products} period={period} confirm={vi.fn()} />);
    expect(getSelfBuyouts).not.toHaveBeenCalled();
    const details = view.container.querySelector("details")!;
    details.open = true; fireEvent(details, new Event("toggle"));
    expect(await screen.findByText("Штук")).toBeInTheDocument();
    expect(getSelfBuyouts).toHaveBeenCalledTimes(1);
  });

  it("shows cards with signed deltas, the footnote and the tables", async () => {
    open();
    expect((await screen.findAllByText(flat("2 510,00 ₽"))).length).toBeGreaterThan(0); // net cost 251000 kop
    expect(screen.getByText("▼ −2 шт (−25%)")).toBeInTheDocument();
    expect(screen.getByText(flat("▲ +1 900,00 ₽ (+190%)"))).toBeInTheDocument();
    expect(screen.getByText("▲ +390,00 ₽ · раньше 0")).toBeInTheDocument(); // null pct: no division by zero
    expect(screen.getByLabelText(/снижение на 2 шт/)).toBeInTheDocument();
    expect(screen.getByText(/Ручной учёт затрат. Данные не отправляются в WB и не влияют на прибыль/)).toBeInTheDocument();
    expect(within(screen.getByRole("table", { name: "Записи журнала" })).getByText("ART-11")).toBeInTheDocument();
  });

  it("refetches with the chosen grouping", async () => {
    open();
    await screen.findByText("Штук");
    fireEvent.click(screen.getByRole("button", { name: "Неделя" }));
    await waitFor(() => expect(getSelfBuyouts).toHaveBeenLastCalledWith(period, "week", expect.anything()));
    expect(screen.getByRole("button", { name: "Неделя" })).toHaveAttribute("aria-pressed", "true");
  });

  it("is read-only for viewers", async () => {
    open("viewer");
    await screen.findByText("Штук");
    expect(screen.queryByRole("button", { name: "Добавить запись" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Удалить запись/ })).not.toBeInTheDocument();
    expect(screen.getByText("Доступ только для чтения.")).toBeInTheDocument();
  });

  it("shows empty and error states", async () => {
    vi.mocked(getSelfBuyouts).mockResolvedValueOnce({ ...report, records: [], groups: [], totals: { ...zero }, deltas: { ...report.deltas, units: { value: 0, previous: 0, delta: 0, delta_pct: null } } });
    open();
    expect(await screen.findByText("За выбранный период записей нет.")).toBeInTheDocument();
    expect(screen.getAllByText("• без изменений").length).toBeGreaterThan(0);
  });

  it("shows a load error with retry", async () => {
    vi.mocked(getSelfBuyouts).mockRejectedValueOnce(new Error("Сеть недоступна"));
    open();
    expect(await screen.findByRole("alert")).toHaveTextContent("Сеть недоступна");
    fireEvent.click(screen.getByRole("button", { name: "Повторить" }));
    expect(await screen.findByText("Штук")).toBeInTheDocument();
  });

  it("ignores a stale response that resolves after a newer request", async () => {
    open();
    await screen.findByText("Штук");
    let late!: (r: BuyoutReport) => void;
    vi.mocked(getSelfBuyouts).mockImplementationOnce(() => new Promise((resolve) => { late = resolve; }));
    fireEvent.click(screen.getByRole("button", { name: "Неделя" }));
    vi.mocked(getSelfBuyouts).mockResolvedValueOnce({ ...report, records: [{ ...report.records[0], vendor_code: "NEW-ONE" }] });
    fireEvent.click(screen.getByRole("button", { name: "Месяц" }));
    expect(await screen.findByText("NEW-ONE")).toBeInTheDocument();
    late({ ...report, records: [{ ...report.records[0], vendor_code: "OLD-STALE" }] });
    await new Promise((r) => setTimeout(r, 20));
    expect(screen.queryByText("OLD-STALE")).not.toBeInTheDocument();
    expect(screen.getByText("NEW-ONE")).toBeInTheDocument();
  });

  it("validates the form field by field and never calls the API for bad input", async () => {
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Добавить запись" }));
    fireEvent.change(screen.getByLabelText("Количество, шт"), { target: { value: "0" } });
    fireEvent.change(screen.getByLabelText("Оплачено, ₽"), { target: { value: "-5" } });
    fireEvent.change(screen.getByLabelText(/Кешбэк/), { target: { value: "abc" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByText("Выберите товар")).toBeInTheDocument();
    expect(screen.getByText(/Количество: целое число/)).toBeInTheDocument();
    expect(screen.getByText(/Введите сумму в рублях, например/)).toBeInTheDocument();
    expect(screen.getByText(/или оставьте пустым/)).toBeInTheDocument();
    expect(screen.getByLabelText(/^Оплачено, ₽/)).toHaveAttribute("aria-invalid", "true");
    expect(createSelfBuyout).not.toHaveBeenCalled();
  });

  it("sends kopecks parsed from comma input and reloads", async () => {
    vi.mocked(createSelfBuyout).mockResolvedValue(report.records[0]);
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Добавить запись" }));
    fireEvent.change(screen.getByLabelText("Товар"), { target: { value: "12" } });
    fireEvent.change(screen.getByLabelText("Количество, шт"), { target: { value: "3" } });
    fireEvent.change(screen.getByLabelText("Оплачено, ₽"), { target: { value: "1 250,5" } });
    fireEvent.change(screen.getByLabelText(/Кешбэк/), { target: { value: "100" } });
    fireEvent.change(screen.getByLabelText(/Метка/), { target: { value: " ручной " } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(createSelfBuyout).toHaveBeenCalledTimes(1));
    expect(vi.mocked(createSelfBuyout).mock.calls[0][0]).toMatchObject({ nm_id: 12, units: 3, buyout_amount_kop: 125050, cashback_kop: 10000, format: "ручной", note: null });
    await waitFor(() => expect(getSelfBuyouts).toHaveBeenCalledTimes(2));
  });

  it("shows server field errors next to the inputs", async () => {
    vi.mocked(createSelfBuyout).mockRejectedValue(new BuyoutError("Проверьте поля формы", { nm_id: "Неизвестный артикул WB" }));
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Добавить запись" }));
    fireEvent.change(screen.getByLabelText("Товар"), { target: { value: "11" } });
    fireEvent.change(screen.getByLabelText("Оплачено, ₽"), { target: { value: "10" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    expect(await screen.findByText("Неизвестный артикул WB")).toBeInTheDocument();
  });

  it("edits a record through the update endpoint with the id", async () => {
    vi.mocked(updateSelfBuyout).mockResolvedValue(report.records[0]);
    open();
    fireEvent.click(await screen.findByRole("button", { name: /Изменить запись от 07.09.2026/ }));
    expect(screen.getByLabelText("Оплачено, ₽")).toHaveValue("2900,00");
    fireEvent.change(screen.getByLabelText("Количество, шт"), { target: { value: "7" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(updateSelfBuyout).toHaveBeenCalledWith(5, expect.objectContaining({ units: 7, nm_id: 11, buyout_amount_kop: 290000 })));
  });

  it("asks for confirmation before delete and keeps the row when declined", async () => {
    const { confirm } = open("owner", vi.fn().mockResolvedValueOnce(false).mockResolvedValueOnce(true));
    const del = await screen.findByRole("button", { name: /Удалить запись от 07.09.2026/ });
    fireEvent.click(del);
    await waitFor(() => expect(confirm).toHaveBeenCalledTimes(1));
    expect(deleteSelfBuyout).not.toHaveBeenCalled();
    vi.mocked(deleteSelfBuyout).mockResolvedValue({ deleted: 5 });
    fireEvent.click(del);
    await waitFor(() => expect(deleteSelfBuyout).toHaveBeenCalledWith(5));
  });

  it("exports the records as CSV", async () => {
    let blob: Blob | undefined;
    URL.createObjectURL = vi.fn((b: Blob) => { blob = b; return "blob:x"; }); URL.revokeObjectURL = vi.fn();
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => undefined);
    open();
    fireEvent.click(await screen.findByRole("button", { name: "Экспорт CSV" }));
    const text = await blob!.text();
    expect(text).toContain("Дата;Артикул продавца;Артикул WB;Шт;Оплачено, ₽;Кешбэк, ₽;Чистые затраты, ₽;Метка;Примечание");
    expect(text).toContain("2026-09-07;ART-11;11;6;2900,00;390,00;2510,00;метка;заметка");
    click.mockRestore();
  });
});
