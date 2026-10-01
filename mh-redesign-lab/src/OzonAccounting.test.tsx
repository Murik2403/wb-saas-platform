import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, fireEvent, waitFor, cleanup } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { OzonAccounting } from "./OzonAccounting";
import { clearShopContext, selectShop, type ShopSession } from "./selected-shop";
const mocks = vi.hoisted(() => ({ session: vi.fn() }));
vi.mock("./data-source", () => ({ API_BASE_URL: "/tenant-api", apiHeaders: () => ({"content-type":"application/json"}), getDashboardSession: mocks.session }));
vi.mock("./selected-shop",async importOriginal=>({...await importOriginal<typeof import('./selected-shop')>(),scopedFetch:(...args:Parameters<typeof fetch>)=>globalThis.fetch(...args)}));
const loaded = { configured:true, loaded:true, source_complete:false, source_collection_complete:true, financial_normalization_verified:false, accounting_complete:false, net_accrual_rub:"100", estimated_cogs_rub:"0", profit_rub:null, known_cost_units:0, sales_and_return_units:2, blockers:["Себестоимость не заполнена"],products:[{sku:"123",offer_id:"Товар",product_id:1,stocks:[],cost_mapping:null}], source_counts:{products:1,stocks:0,fbo:0,fbs:0,returns:0,accruals:1}, fees:[] };
const response = (body: unknown, ok=true) => ({ ok, status:ok?200:422, json:async()=>body }) as Response;
beforeEach(() => {
  clearShopContext();
  mocks.session.mockReset().mockResolvedValue({mode:"tenant",user:{role:"owner"}});
  vi.stubGlobal("fetch",vi.fn().mockResolvedValue(response(loaded)));
});
afterEach(() => {cleanup();clearShopContext();vi.unstubAllGlobals();});
describe("Ozon accounting", () => {
  it("shows separate source quantities without pretending movements or returns are verified", async () => {
    vi.mocked(fetch).mockResolvedValue(response({...loaded,posting_accrual_evidence:{loaded:true,source_row_count:2,scope:{requested_number_count:1,missing_number_count:0},rows:[
      {source_occurrence_key:"one",posting_number:"fictional-posting",accrual_date:"2026-09-01",sku_text:"123",type_id_text:"1",quantity_text:"2",accrued_rub:"-12.34",seller_price_rub:"100.00"},
      {source_occurrence_key:"two",posting_number:"fictional-posting",accrual_date:"2026-09-01",sku_text:null,type_id_text:"1",quantity_text:null,accrued_rub:"0.20",seller_price_rub:null}
    ]}}));
    render(<OzonAccounting />);
    expect(await screen.findByRole("heading",{name:"Исходные финансовые строки отправлений Ozon"})).toBeInTheDocument();
    expect(screen.getByText(/Количество из источника не подтверждает движение/)).toBeInTheDocument();
    expect(screen.getAllByText("fictional-posting")).toHaveLength(2);
    expect(screen.getByText("-12,34 ₽")).toBeInTheDocument();
    expect(screen.getAllByText("не указано / не распознано").length).toBeGreaterThan(0);
    expect(vi.mocked(fetch).mock.calls.every(call=>(call[1] as RequestInit).method==="GET")).toBe(true);
  });
  it("shows reconciled fees and rejected rows without allowing ledger confirmation", async () => {
    vi.mocked(fetch).mockResolvedValue(response({...loaded, fee_preview: {reconciled_total_rub:"-0.10",rows:[
      {source_occurrence_key:"a",accrual_id:7,date:"2026-09-01",category:"NON_ITEM",reconciled:true,component_sum_rub:"-0.10",components:[{source_path:"non_item_fee",type_id:1,name:"Эквайринг",sku:null,amount_rub:"-0.10"}]},
      {source_occurrence_key:"b",accrual_id:7,date:"2026-09-01",category:"POSTING",reconciled:false,component_sum_rub:null,components:[],reason:"Продажи не поддерживаются"}
    ]}}));
    render(<OzonAccounting />);
    expect(await screen.findByRole("heading",{name:"Предварительная сверка сборов Ozon"})).toBeInTheDocument();
    expect(screen.getByText(/Сверено строк: 1 из 2/)).toBeInTheDocument();
    expect(screen.getByText("Продажи не поддерживаются")).toBeInTheDocument();
    expect(screen.getByText(/Эквайринг: -0,1/)).toBeInTheDocument();
    expect(screen.getByText(/запись в документальный учёт недоступна/)).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.every(call=>(call[1] as RequestInit).method==="GET")).toBe(true);
  });
  it("preserves every kopeck of large signed preview totals and components", async () => {
    const amount = "-99999999999999999.99";
    vi.mocked(fetch).mockResolvedValue(response({...loaded, fee_preview:{reconciled_total_rub:amount,rows:[
      {source_occurrence_key:"large",accrual_id:7,date:"2026-09-01",category:"NON_ITEM",reconciled:true,component_sum_rub:amount,
       components:[{source_path:"non_item_fee",type_id:1,name:"Большой сбор",sku:null,amount_rub:amount}]}
    ]}}));
    render(<OzonAccounting />);
    await screen.findByRole("heading",{name:"Предварительная сверка сборов Ozon"});
    expect(screen.getByText(/Сумма сборов, совпавших/)).toHaveTextContent("-99 999 999 999 999 999,99 ₽");
    expect(screen.getByText(/Большой сбор:/)).toHaveTextContent("-99 999 999 999 999 999,99 ₽");
    expect(screen.getAllByRole("cell").filter(cell => cell.textContent?.replace(/\u00a0/g, " ") === "-99 999 999 999 999 999,99 ₽")).toHaveLength(1);
  });
  it("distinguishes a complete collection from unverified monetary interpretation", async () => {
    vi.mocked(fetch).mockResolvedValue(response({...loaded, source_complete:false, source_collection_complete:true, financial_normalization_verified:false}));
    render(<OzonAccounting />);
    await screen.findByText("Товар");
    expect(screen.getByRole("status")).toHaveTextContent("Загрузка API: все страницы загружены");
    expect(screen.getByRole("status")).toHaveTextContent("Денежная расшифровка API: не подтверждена");
    expect(screen.getByRole("status")).not.toHaveTextContent("неполная загрузка");
    expect(screen.getByText(/Прибыль: не рассчитано/)).toBeInTheDocument();
  });
  it.each([false, undefined])("does not infer collection completeness from legacy source_complete: %s", async collection => {
    vi.mocked(fetch).mockResolvedValue(response({...loaded, source_complete:true, source_collection_complete:collection}));
    render(<OzonAccounting />);
    await screen.findByText("Товар");
    expect(screen.getByRole("status")).toHaveTextContent("Загрузка API: полнота не подтверждена");
    expect(screen.getByRole("status")).not.toHaveTextContent("все страницы загружены");
  });
  it("does not fetch or render primary Ozon figures for a verified secondary WB shop",async()=>{
    const selected:ShopSession={role:'owner',selected_shop_id:0,shops:[{id:0,name:'Primary',verified_identity:null,snapshot_revision:null,active:true,can_write:true,allowed_actions:[]},{id:2,name:'WB second',verified_identity:'a'.repeat(64),snapshot_revision:1,active:true,can_write:true,allowed_actions:[]}],route_scopes:[]};
    selectShop(selected,2);render(<OzonAccounting />);
    expect(screen.getByText(/Учёт Ozon доступен в основном кабинете/)).toBeInTheDocument();
    expect(screen.getByRole('button',{name:'Переключиться на основной магазин'})).toBeInTheDocument();
    expect(fetch).not.toHaveBeenCalled();expect(mocks.session).not.toHaveBeenCalled();expect(screen.queryByText('Товар')).not.toBeInTheDocument();
  });
  it("unmounts previously loaded primary figures when rendered in a secondary context",async()=>{
    const {rerender}=render(<OzonAccounting />);await screen.findByText('Товар');
    const before=vi.mocked(fetch).mock.calls.length;
    selectShop({role:'owner',selected_shop_id:0,shops:[{id:0,name:'Primary',verified_identity:null,snapshot_revision:null,active:true,can_write:true,allowed_actions:[]},{id:2,name:'Second',verified_identity:'a'.repeat(64),snapshot_revision:1,active:true,can_write:true,allowed_actions:[]}],route_scopes:[]},2);
    rerender(<OzonAccounting />);expect(screen.queryByText('Товар')).not.toBeInTheDocument();expect(screen.getByText(/Учёт Ozon доступен в основном кабинете/)).toBeInTheDocument();
    await Promise.resolve();expect(fetch).toHaveBeenCalledTimes(before);
  });
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
