import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TaxReconciliationPanel, buildTaxInput, taxReconciliationCsv } from "./TaxReconciliationPanel";
import { scopedFetch } from "./selected-shop";

vi.mock("./selected-shop", () => ({ scopedFetch: vi.fn() }));
const fetchMock = vi.mocked(scopedFetch);
const values = { month: "2026-09", regime: "ausn_income", source: "", confirmed: false, received: null, offset: null, compensation: null, other_income: null, refunds: null, profit_before_tax: null, fns_assessed: null, paid: null };
const empty = { values, revision: 0, can_write: true, calculation: { status: "incomplete", missing: ["received", "offset", "compensation", "other_income", "refunds"], base: null, estimate: null, after_estimate: null, fns_difference: null, after_fns: null, assessed_minus_paid: null, negative_base: false }, history: [] };
const response = (data: unknown) => new Response(JSON.stringify(data), { status: 200, headers: { "content-type": "application/json" } });
beforeEach(() => { fetchMock.mockReset(); fetchMock.mockImplementation(async () => response(empty)); });

describe("АУСН сверка", () => {
  it("distinguishes blanks from zero and rejects invalid money", () => {
    const cells = Object.fromEntries(Object.keys(values).filter(key => !["month", "regime", "source", "confirmed"].includes(key)).map(key => [key, ""])) as Parameters<typeof buildTaxInput>[0];
    expect(buildTaxInput(cells).received).toBeNull();
    expect(buildTaxInput({ ...cells, received: "0", offset: "1,25" })).toMatchObject({ received: 0, offset: 1.25 });
    expect(() => buildTaxInput({ ...cells, received: "-1" })).toThrow();
    expect(() => buildTaxInput({ ...cells, received: "1.001" })).toThrow();
  });
  it("saves explicit numbers and preserves confirmation", async () => {
    render(<TaxReconciliationPanel />);
    await screen.findByText("Сохранить и рассчитать");
    fireEvent.click(screen.getByText(/Налоги · расчёт/));
    fireEvent.change(screen.getByLabelText(/^Поступления от маркетплейса/), { target: { value: "100,50" } });
    fireEvent.change(screen.getByLabelText("Основание / документы"), { target: { value: "bank" } });
    fireEvent.click(screen.getByLabelText(/Проверил даты/));
    fetchMock.mockResolvedValueOnce(response({ ...empty, revision: 1 }));
    fireEvent.click(screen.getByText("Сохранить и рассчитать"));
    await screen.findByText(/Сверка сохранена/);
    const request = JSON.parse(String(fetchMock.mock.calls[1][1]?.body));
    expect(request).toMatchObject({ received: 100.50, offset: null, confirmed: true, revision: 0, source: "bank", regime: "ausn_income" });
  });
  it("reloads a calendar month and never submits on selection", async () => {
    render(<TaxReconciliationPanel />);
    await screen.findByText("Сохранить и рассчитать");
    fireEvent.change(screen.getByLabelText("Месяц сверки"), { target: { value: "2026-09" } });
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(String(fetchMock.mock.calls[1][0])).toContain("month=2026-09");
    expect(fetchMock.mock.calls.every(call => call[1]?.method !== "PUT")).toBe(true);
  });
  it("hides save for viewer and exports missing values without turning them into zero", async () => {
    fetchMock.mockResolvedValue(response({ ...empty, can_write: false }));
    render(<TaxReconciliationPanel />);
    await screen.findByText(/У вас доступ для просмотра/);
    expect(screen.queryByText("Сохранить и рассчитать")).toBeNull();
    expect(taxReconciliationCsv({ ...empty, values: { ...values, source: '=SUM(A1)' } })).toContain("Не указано");
    expect(taxReconciliationCsv({ ...empty, values: { ...values, source: '=SUM(A1)' } })).toContain("'=SUM(A1)");
  });
});

describe("Налоговый подраздел", () => {
  it.each(["ausn_income_expense", "usn_income", "usn_income_expense", "osno_ip", "osno_company"])("selects %s and sends the right period", async regime => {
    render(<TaxReconciliationPanel />);
    await screen.findByText("Сохранить и рассчитать");
    fireEvent.click(screen.getByText(/Налоги · расчёт/));
    fireEvent.change(screen.getByLabelText("Налоговый режим"), { target: { value: regime } });
    fireEvent.change(screen.getByLabelText("Основание / документы"), { target: { value: "verified" } });
    fireEvent.click(screen.getByLabelText(/Проверил даты/));
    fireEvent.click(screen.getByText("Сохранить и рассчитать"));
    await screen.findByText(/Сверка сохранена/);
    const body=JSON.parse(String(fetchMock.mock.calls[1][1]?.body));
    expect(body.regime).toBe(regime);
    expect(body.period_mode).toBe(regime.startsWith("ausn")?"month":"ytd");
    expect(body.rate).toBe(regime==="osno_ip"?null:regime==="ausn_income_expense"?20:regime==="usn_income"?6:regime==="usn_income_expense"?15:25);
  });
  it("separates reconciliation/history and revokes confirmation when changing numbers",async()=>{
    render(<TaxReconciliationPanel />);await screen.findByText("Сохранить и рассчитать");
    fireEvent.click(screen.getByLabelText(/Проверил даты/));
    expect(screen.getByText("Сохранить и рассчитать")).not.toBeDisabled();
    fireEvent.change(screen.getByLabelText(/^Поступления от маркетплейса/),{target:{value:"100"}});
    expect(screen.getByText("Сохранить и рассчитать")).toBeDisabled();
    fireEvent.click(screen.getByRole("tab",{name:"Начисление и оплата"}));
    expect(screen.getByLabelText(/^Начислено по документам/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/^Поступления от маркетплейса/)).toBeNull();
    fireEvent.click(screen.getByRole("tab",{name:"История"}));
    expect(screen.getByText("Сохранённых расчётов пока нет.")).toBeInTheDocument();
    expect(screen.queryByText("Сохранить и рассчитать")).toBeNull();
  });
  it("allows VAT deductions only for general rates and keeps CSV regime metadata",async()=>{
    render(<TaxReconciliationPanel />);await screen.findByText("Сохранить и рассчитать");
    fireEvent.change(screen.getByLabelText("Налоговый режим"),{target:{value:"usn_income"}});
    fireEvent.change(screen.getByLabelText(/^Режим НДС/),{target:{value:"special5"}});
    expect(screen.queryByLabelText(/^НДС к вычету/)).toBeNull();
    fireEvent.change(screen.getByLabelText(/^Режим НДС/),{target:{value:"general22"}});
    expect(screen.getByLabelText(/^НДС к вычету/)).toBeInTheDocument();
    expect(taxReconciliationCsv({...empty,values:{...values,regime:"usn_income",period_mode:"ytd",rate:6,vat_mode:"special5"}})).toContain("УСН «Доходы»");
  });
});

it("supports keyboard navigation between tax tabs",async()=>{
  render(<TaxReconciliationPanel />);await screen.findByText("Сохранить и рассчитать");
  fireEvent.keyDown(screen.getByRole("tab",{name:"Расчёт"}),{key:"ArrowRight"});
  expect(screen.getByRole("tab",{name:"Начисление и оплата"})).toHaveAttribute("aria-selected","true");
  fireEvent.keyDown(screen.getByRole("tab",{name:"Начисление и оплата"}),{key:"End"});
  expect(screen.getByRole("tab",{name:"История"})).toHaveAttribute("aria-selected","true");
});
