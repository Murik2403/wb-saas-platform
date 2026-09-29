import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { OzonAccounting } from "./OzonAccounting";
const mocks = vi.hoisted(() => ({ session: vi.fn() }));
vi.mock("./data-source", () => ({ API_BASE_URL: "/tenant-api", apiHeaders: () => ({"content-type":"application/json"}), getDashboardSession: mocks.session }));
const loaded = { configured:true, loaded:true, source_complete:true, accounting_complete:false, net_accrual_rub:"100", estimated_cogs_rub:"0", profit_rub:null, known_cost_units:0, sales_and_return_units:2, blockers:["Себестоимость не заполнена"],products:[{sku:"123",offer_id:"Товар",product_id:1,stocks:[],cost_mapping:null}], source_counts:{products:1,stocks:0,fbo:0,fbs:0,returns:0,accruals:1}, fees:[] };
const response = (body: unknown, ok=true) => ({ ok, status:ok?200:422, json:async()=>body }) as Response;
beforeEach(() => {
  mocks.session.mockReset().mockResolvedValue({mode:"tenant",user:{role:"owner"}});
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue(response(loaded)));
});
afterEach(() => {cleanup();vi.unstubAllGlobals();});
describe("Ozon accounting", () => {
  it("never renders unknown profitability as zero and displays source completeness separately",async () => {
    render(<OzonAccounting />);
    expect(await screen.findByText(/Прибыль: не рассчитано/)).toBeInTheDocument();
    expect(screen.getByText(/все страницы загружены/)).toHaveTextContent("предварительный");
    expect(screen.getByText("Себестоимость не заполнена")).toBeInTheDocument();
  });
  it("keeps viewer controls read-only and never starts a remote sync automatically",async () => {
    mocks.session.mockResolvedValue({mode:"tenant",user:{role:"viewer"}});
    render(<OzonAccounting />);
    await screen.findByText("Товар");
    expect(screen.getByRole("button",{name:"Загрузить данные Ozon"})).toBeDisabled();
    expect(screen.getByLabelText("Стоимость Ozon 123")).toBeDisabled();
    expect(vi.mocked(fetch).mock.calls.every(call => (call[1] as RequestInit).method === "GET")).toBe(true);
  });
  it("explicit manual save includes the SKU and does not guess a WB mapping",async () => {
    const spy=vi.mocked(fetch);
    render(<OzonAccounting />); await screen.findByText("Товар");
    await waitFor(()=>expect(screen.getByLabelText("Стоимость Ozon 123")).not.toBeDisabled());
    fireEvent.change(screen.getByLabelText("Стоимость Ozon 123"),{target:{value:"12,50"}});
    fireEvent.click(screen.getByRole("button",{name:"Сохранить стоимость"}));
    await waitFor(()=>expect(spy.mock.calls.some(call=>(call[1] as RequestInit).method==="PUT")).toBe(true));
    const saved=spy.mock.calls.find(call=>(call[1] as RequestInit).method==="PUT")!;
    expect(JSON.parse((saved[1] as RequestInit).body as string)).toEqual({sku:"123",mode:"manual",unit_cost:"12.50"});
  });
  it("a sync error keeps the previous report visible and explains no partial replacement",async () => {
    vi.mocked(fetch).mockImplementation(async (_url,init) => (init as RequestInit).method==="POST" ? response({detail:"Ozon HTTP 429"},false) : response(loaded));
    render(<OzonAccounting />); await screen.findByText("Товар");
    await waitFor(()=>expect(screen.getByRole("button",{name:"Загрузить данные Ozon"})).not.toBeDisabled());
    fireEvent.click(screen.getByRole("button",{name:"Загрузить данные Ozon"}));
    expect(await screen.findByRole("alert")).toHaveTextContent("не заменены частично");
    expect(screen.getByText("Товар")).toBeInTheDocument();
  });
});
