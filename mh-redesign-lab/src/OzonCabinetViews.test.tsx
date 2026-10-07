import { cleanup,fireEvent,render,screen,waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach,describe,expect,it,vi } from "vitest";
import { OzonCabinetViews } from "./OzonCabinetViews";
const fetch=vi.hoisted(()=>vi.fn());
vi.mock("./selected-shop",()=>({scopedFetch:fetch}));
vi.mock("./OzonProductInsights",()=>({OzonProductInsights:()=> <p>Product insights</p>}));
vi.mock("./OzonAdsAnalyticsPanel",()=>({OzonAdsAnalyticsPanel:()=> <p>Performance analytics</p>}));
vi.mock("./OzonMonthlyPanel",()=>({OzonMonthlyPanel:()=> <p>Monthly Ozon</p>}));
vi.mock("./OzonHistoricalPnl",()=>({OzonHistoricalPnl:()=> <p>Historical Ozon</p>}));
vi.mock("./OzonNormalizationPanel",()=>({OzonNormalizationPanel:()=> <p>Normalization Ozon</p>}));
vi.mock("./OzonRealizationSource",()=>({OzonRealizationSource:()=> <p>Realization Ozon</p>}));
vi.mock("./CashflowPanel",()=>({CashflowPanel:()=> <p>Common cashflow</p>}));
vi.mock("./CashAccountsPanel",()=>({CashAccountsPanel:()=> <p>Common accounts</p>}));
vi.mock("./ManufacturingCosts",()=>({ManufacturingCosts:()=> <p>Common manufacturing</p>}));
vi.mock("./OzonPriceControls",()=>({OzonPriceControls:()=> <p>Ozon price drafts</p>}));
const props={start:"2026-09-01",end:"2026-09-30",canWrite:false,isOwner:false,role:"viewer"};

it('manager never gains connection or grant forms from an incorrect owner boolean but can edit product costs',async()=>{
 fetch.mockResolvedValue(response({configured:true,products:[{sku:'123',offer_id:'A'}],items:[],loaded:true}));
 render(<OzonCabinetViews {...props} page="settings" canWrite isOwner role="manager"/>);
 await screen.findByText('Подключение: настроено.');
 expect(screen.queryByLabelText('Api-Key Ozon')).toBeNull();expect(screen.queryByLabelText('Аккаунт участника')).toBeNull();
 expect(screen.getByLabelText('Себестоимость SKU 123')).toBeEnabled();
 expect(screen.getByRole('button',{name:'Загрузить данные Seller за период'})).toBeEnabled();
});

it('a downgraded role clears Seller secrets before ownership is restored',async()=>{
 fetch.mockResolvedValue(response({configured:true,products:[],items:[],loaded:false}));
 const view=render(<OzonCabinetViews {...props} page="settings" canWrite isOwner role="owner"/>);
 await userEvent.type(screen.getByLabelText('Api-Key Ozon'),'synthetic-draft');
 view.rerender(<OzonCabinetViews {...props} page="settings" canWrite role="manager"/>);
 view.rerender(<OzonCabinetViews {...props} page="settings" canWrite isOwner role="owner"/>);
 expect(screen.getByLabelText('Api-Key Ozon')).toHaveValue('');
 expect(fetch.mock.calls.every(([,init])=>init.method==='GET')).toBe(true);
});
const snapshot={marketplace:"ozon",loaded:false,synced_at:null,blockers:[],items:[],metrics:{order_units:null,document_profit_rub:null}};
const response=(body:unknown,status=200)=>({ok:status<400,status,json:async()=>body});
afterEach(()=>{cleanup();fetch.mockReset();});
describe("Separate Ozon cabinet destinations",()=>{
 it("requests only Ozon overview and keeps unknown metrics distinct from zero",async()=>{fetch.mockResolvedValue(response(snapshot));render(<OzonCabinetViews {...props} page="today"/>);await screen.findByText(/Источник ещё не загружен/);expect(screen.getByText("Нет данных")).toBeTruthy();expect(screen.getByText("не рассчитано")).toBeTruthy();expect(fetch.mock.calls.every(([url])=>String(url).includes("/ozon-cabinet/overview"))).toBe(true);});
 it("rejects a mismatched marketplace body",async()=>{fetch.mockResolvedValue(response({...snapshot,marketplace:"wb"}));render(<OzonCabinetViews {...props} page="stock"/>);await screen.findByRole("alert");expect(screen.getByRole("alert").textContent).toMatch(/не соответствует Ozon/);});
 it("shows current stock scope and known zero without hiding missing reserves",async()=>{fetch.mockResolvedValue(response({...snapshot,loaded:true,items:[{sku:"s1",offer_id:"article",present_units:0,reserved_units:null}],metrics:{present_units:0,reserved_units:null}}));render(<OzonCabinetViews {...props} page="stock"/>);await screen.findByText("article");expect(screen.getAllByText("0").length).toBe(2);expect(screen.getByText(/не превращает его в исторические/)).toBeTruthy();});
 it("filters FBS orders locally through explicit Ozon source request",async()=>{fetch.mockResolvedValue(response(snapshot));render(<OzonCabinetViews {...props} page="supply"/>);await screen.findByLabelText("Схема отправлений");await userEvent.selectOptions(screen.getByLabelText("Схема отправлений"),"fbs");await waitFor(()=>expect(fetch.mock.calls.some(([u])=>String(u).includes("kind=fbs"))).toBe(true));expect(fetch.mock.calls.some(([u])=>String(u).includes("/returns"))).toBe(true);});
 it("aborts old period and rejects delayed response decoding",async()=>{let resolve!:(x:unknown)=>void;fetch.mockImplementationOnce(()=>new Promise(r=>resolve=r)).mockResolvedValue(response(snapshot));const view=render(<OzonCabinetViews {...props} page="today"/>);const signal=fetch.mock.calls[0][1].signal;view.rerender(<OzonCabinetViews {...props} start="2026-10-01" page="today"/>);expect(signal.aborted).toBe(true);resolve(response({...snapshot,metrics:{order_units:777}}));await screen.findByText(/Источник ещё не загружен/);expect(screen.queryByText("777")).toBeNull();});
 it("viewer sees no credential form or cost write controls",async()=>{fetch.mockResolvedValue(response({configured:true,products:[],loaded:false}));render(<OzonCabinetViews {...props} page="settings"/>);await screen.findByText("Подключение: настроено.");expect(screen.queryByLabelText("Api-Key Ozon")).toBeNull();expect(fetch.mock.calls.every(([,init])=>init.method==="GET")).toBe(true);});
 it("owner submits Seller credentials explicitly and clears password",async()=>{fetch.mockResolvedValue(response({configured:false,products:[],loaded:false,items:[]}));const store=vi.spyOn(Storage.prototype,"setItem");render(<OzonCabinetViews {...props} page="settings" canWrite isOwner role="owner"/>);await userEvent.type(screen.getByLabelText("Client-Id Ozon"),"123");await userEvent.type(screen.getByLabelText("Api-Key Ozon"),"secret");await userEvent.click(screen.getByRole("button",{name:"Сохранить ключ Seller"}));await screen.findByText(/Сохранено защищённо/);expect((screen.getByLabelText("Api-Key Ozon") as HTMLInputElement).value).toBe("");expect(fetch.mock.calls.some(([u,i])=>String(u).endsWith("/settings/ozon")&&i.method==="POST")).toBe(true);expect(store).not.toHaveBeenCalled();store.mockRestore();});
 it("surfaces HTTP429 without retry or write",async()=>{fetch.mockResolvedValue(response({detail:"Источник ограничил частоту запросов"},429));render(<OzonCabinetViews {...props} page="control"/>);await screen.findByRole("alert");expect(fetch).toHaveBeenCalledTimes(1);});
 it("owner grants separate Ozon access explicitly without inheriting WB rights",async()=>{fetch.mockResolvedValue(response({configured:true,products:[],items:[],loaded:false}));render(<OzonCabinetViews {...props} page="settings" canWrite isOwner role="owner"/>);await userEvent.type(screen.getByLabelText("Аккаунт участника"),"teammate@example.com");await userEvent.click(screen.getByLabelText("Разрешить запись по роли"));await userEvent.click(screen.getByRole("button",{name:"Сохранить доступ Ozon"}));await screen.findByText("Доступ Ozon сохранён.");const call=fetch.mock.calls.find(([u,i])=>String(u).endsWith("/ozon/access")&&i.method==="PUT");expect(JSON.parse(call![1].body)).toEqual({account:"teammate@example.com",can_write:true,active:true});});
 it("manager cannot edit Seller secrets or grants, and changing period wipes drafts",async()=>{fetch.mockResolvedValue(response({configured:true,products:[],items:[],loaded:false}));const v=render(<OzonCabinetViews {...props} page="settings" canWrite role="manager"/>);await screen.findByText("Подключение: настроено.");expect(screen.queryByLabelText("Аккаунт участника")).toBeNull();expect(screen.queryByLabelText("Api-Key Ozon")).toBeNull();v.rerender(<OzonCabinetViews {...props} page="settings" canWrite isOwner role="owner"/>);await userEvent.type(screen.getByLabelText("Api-Key Ozon"),"do-not-retain");v.rerender(<OzonCabinetViews {...props} page="settings" start="2026-10-01" canWrite isOwner role="owner"/>);expect((screen.getByLabelText("Api-Key Ozon") as HTMLInputElement).value).toBe("");});
 it("composes dedicated financial and shared resources without claiming Ozon identity",async()=>{render(<OzonCabinetViews {...props} page="finance"/>);expect(await screen.findByText("Monthly Ozon")).toBeTruthy();expect(screen.getByText("Common accounts")).toBeTruthy();expect(screen.getByText(/Счета общие для WB и Ozon/)).toBeTruthy();
 expect(screen.queryByText('Normalization Ozon')).toBeNull();expect(screen.queryByText('Historical Ozon')).toBeNull();expect(screen.queryByText('Realization Ozon')).toBeNull();
 const details=screen.getByText('Сверка начислений API и документального реестра').closest('details')!;details.open=true;fireEvent(details,new Event('toggle'));
 expect(await screen.findByText('Normalization Ozon')).toBeTruthy();expect(screen.queryByText('Historical Ozon')).toBeNull();
 });
 it("discloses unsupported ad automation and mounts shared manufacturing separately",()=>{const v=render(<OzonCabinetViews {...props} page="agents"/>);expect(screen.getByText(/Автоматическое управление рекламным бюджетом/)).toBeTruthy();expect(fetch).not.toHaveBeenCalled();v.rerender(<OzonCabinetViews {...props} page="production"/>);expect(screen.getByText(/Физический каталог использует идентификаторы WB/)).toBeTruthy();expect(screen.getByText("Common manufacturing")).toBeTruthy();});
});
