import {beforeEach,describe,expect,it,vi} from "vitest";
import {fireEvent,render,screen,waitFor} from "@testing-library/react";
import {CashflowPanel} from "./CashflowPanel";
import {getCashflow,createCashEntry,changeCashEntry,type CashReport} from "./cashflow-source";
vi.mock("./cashflow-source",()=>({getCashflow:vi.fn(),createCashEntry:vi.fn(),changeCashEntry:vi.fn()}));
const report: CashReport={items:[],events:[],opening_balance_minor:100000,actual_closing_minor:120000,forecast_closing_minor:71250,actual_inflow_minor:20000,actual_outflow_minor:0,overdue_planned_minor:0,opening_confirmed:true,period_before_opening:false,daily:[]};
describe("CashflowPanel",()=>{
  beforeEach(()=>{vi.resetAllMocks();vi.mocked(getCashflow).mockResolvedValue(report);});
  it("shows separate actual and forecast with explicit manual coverage",async()=>{
    render(<CashflowPanel role="owner"/>);
    expect(await screen.findByText("Остаток с планами")).toBeInTheDocument();
    expect(screen.getByText(/Выручка и прибыль WB не означают/)).toBeInTheDocument();
    expect(screen.getByText("712,50 ₽")).toBeInTheDocument();
  });
  it("hides writes for read-only and shop-floor roles",async()=>{
    render(<CashflowPanel role="warehouse"/>);
    expect(await screen.findByText(/Доступ только для чтения/)).toBeInTheDocument();
    expect(screen.queryByRole("button",{name:"Добавить операцию"})).not.toBeInTheDocument();
  });
  it("does not show balances for a period before the opening anchor",async()=>{
    vi.mocked(getCashflow).mockResolvedValue({...report,opening_confirmed:false,period_before_opening:true});
    render(<CashflowPanel role="owner"/>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Период начинается раньше начального остатка");
    expect(screen.queryByText("Остаток по факту")).not.toBeInTheDocument();
  });
  it("confirms a planned payment with selected actual date",async()=>{
    vi.mocked(getCashflow).mockResolvedValue({...report,items:[{id:7,kind:"movement",direction:"outflow",status:"planned",planned_date:"2026-09-10",actual_date:null,amount_minor:48750,amount:"37.5",currency:"CNY",exchange_rate:"13",category:"procurement",reference:"PO-1"}]});
    render(<CashflowPanel role="manager"/>);
    const button=await screen.findByRole("button",{name:"Оплачено / получено"});
    fireEvent.change(screen.getByLabelText("Дата оплаты операции 7"),{target:{value:"2026-09-15"}});
    fireEvent.click(button);
    await waitFor(()=>expect(changeCashEntry).toHaveBeenCalledWith(7,"actual","2026-09-15"));
  });
  it("shows load error and lets user retry",async()=>{
    vi.mocked(getCashflow).mockRejectedValueOnce(new Error("Сеть недоступна"));
    render(<CashflowPanel role="viewer"/>);
    expect(await screen.findByRole("alert")).toHaveTextContent("Сеть недоступна");
    fireEvent.click(screen.getByRole("button",{name:"Обновить календарь"}));
    expect(await screen.findByText("Остаток с планами")).toBeInTheDocument();
  });
  it("retries the identical request after an uncertain response",async()=>{
    vi.mocked(createCashEntry).mockRejectedValueOnce(new Error("Связь прервана"));
    render(<CashflowPanel role="owner"/>);
    await screen.findByText("Денежных операций пока нет.");
    fireEvent.change(screen.getByLabelText("Сумма в валюте"),{target:{value:"125.5"}});
    fireEvent.click(screen.getByRole("button",{name:"Добавить операцию"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("Связь прервана");
    fireEvent.click(screen.getByRole("button",{name:"Добавить операцию"}));
    await waitFor(()=>expect(createCashEntry).toHaveBeenCalledTimes(2));
    expect(vi.mocked(createCashEntry).mock.calls[0][0]).toEqual(vi.mocked(createCashEntry).mock.calls[1][0]);
  });
});
