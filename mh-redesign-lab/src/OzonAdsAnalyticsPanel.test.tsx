import { cleanup,render,screen,waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach,describe,expect,it,vi } from "vitest";
import { OzonAdsAnalyticsPanel,formatOzonAdMetric } from "./OzonAdsAnalyticsPanel";

const props={start:"2026-09-01",end:"2026-09-02",canWrite:true,isOwner:true};
const totals={spend:null,views:null,clicks:null,orders:null,ad_revenue:null,ctr:null,cpc:null,cpm:null,cpo:null,drr:null};
const empty={loaded:false,has_data:false,source_complete:false,totals,campaigns:[],by_day:[],by_day_available:false};
const response=(body:unknown,status=200)=>({ok:status>=200&&status<300,status,json:async()=>body,blob:async()=>new Blob(["csv"])});
function mockFetch(configured=false,loaded:unknown=empty,post?: (url:string,init?:RequestInit)=>unknown) {
  const fetch=vi.fn(async(url:string,init?:RequestInit)=>{
    if (init?.method && init.method!=="GET") return post ? post(url,init) : response({configured:true});
    if(url.endsWith("/credentials"))return response({configured});
    if(url.includes("/export.csv"))return response({});
    return response(loaded);
  });vi.stubGlobal("fetch",fetch);return fetch;
}
afterEach(()=>{cleanup();vi.unstubAllGlobals();vi.restoreAllMocks();});
describe("Ozon ad reporting",()=>{
  it("renders unknown metrics without fabricated zero and hides write forms from viewers",async()=>{
    mockFetch();render(<OzonAdsAnalyticsPanel {...props} canWrite={false} isOwner={false}/>);
    await screen.findByText(/не означает нулевой расход/);
    expect(screen.getAllByText("—").length).toBe(10);
    expect(screen.queryByLabelText("Client Secret Performance")).toBeNull();
    expect(screen.queryByRole("button",{name:"Создать рекламный отчёт"})).toBeNull();
  });
  it("keeps zero distinct from missing",()=>{
    expect(formatOzonAdMetric(null,"money")).toBe("—");expect(formatOzonAdMetric(0,"money")).toBe("0 ₽");expect(formatOzonAdMetric(Number.NaN,"percent")).toBe("—");
    expect(formatOzonAdMetric("9007999999999909.93","money")).toBe("9\u00a0007\u00a0999\u00a0999\u00a0999\u00a0909,93 ₽");
  });
  it("manager can request reports but never change protected credentials",async()=>{
    mockFetch(true);render(<OzonAdsAnalyticsPanel {...props} isOwner={false}/>);
    await screen.findByLabelText("ID кампаний Performance");
    expect(screen.queryByLabelText("Client Secret Performance")).toBeNull();
    expect(screen.getByRole("button",{name:"Создать рекламный отчёт"})).toBeTruthy();
  });
  it("owner secret field is password and clears after secure save without storage",async()=>{
    const storage=vi.spyOn(Storage.prototype,"setItem");const fetch=mockFetch();render(<OzonAdsAnalyticsPanel {...props}/>);
    await screen.findByText(/не подключён/);await userEvent.click(screen.getByText("Подключить Performance API"));
    const input=screen.getByLabelText("Client Secret Performance") as HTMLInputElement;expect(input.type).toBe("password");
    await userEvent.type(screen.getByLabelText("Client ID Performance"),"performance-id");await userEvent.type(input,"private-secret");await userEvent.click(screen.getByRole("button",{name:"Сохранить ключ Performance"}));
    await screen.findByText(/сохранён защищённо/);expect(input.value).toBe("");
    expect(storage).not.toHaveBeenCalled();expect(fetch.mock.calls.some(([,init])=>init?.method==="PUT" && String(init.body).includes("private-secret"))).toBe(true);
  });
  it("validates selected campaigns and performs only explicit submit and one poll",async()=>{
    const fetch=mockFetch(true,empty,(url)=>response({id:"job1",status:url.endsWith("/poll")?"complete":"pending",error_code:null,polls:0}));
    render(<OzonAdsAnalyticsPanel {...props}/>);const input=await screen.findByLabelText("ID кампаний Performance");
    await userEvent.type(input,"101, 101");await userEvent.click(screen.getByRole("button",{name:"Создать рекламный отчёт"}));await screen.findByRole("alert");expect(fetch.mock.calls.filter(([,init])=>init?.method==="POST")).toHaveLength(0);
    await userEvent.clear(input);await userEvent.type(input,"101,102");await userEvent.click(screen.getByRole("button",{name:"Создать рекламный отчёт"}));
    const poll=await screen.findByRole("button",{name:"Проверить готовность"});expect(fetch.mock.calls.filter(([,init])=>init?.method==="POST")).toHaveLength(1);
    await userEvent.click(poll);await screen.findByText("Рекламный отчёт загружен.");expect(fetch.mock.calls.filter(([,init])=>init?.method==="POST")).toHaveLength(2);
  });
  it("429 preserves loaded metrics and offers a clear retry message",async()=>{
    mockFetch(true,{...empty,loaded:true,source_complete:true,totals:{...totals,spend:42}},()=>response({detail:{code:"rate_limit"}},429));render(<OzonAdsAnalyticsPanel {...props}/>);
    const input=await screen.findByLabelText("ID кампаний Performance");await userEvent.type(input,"101");await userEvent.click(screen.getByRole("button",{name:"Создать рекламный отчёт"}));
    expect((await screen.findByRole("alert")).textContent).toMatch(/Лимит Performance API/);expect(screen.getByText("42 ₽")).toBeTruthy();
  });
  it("downloads CSV through an authenticated fetch rather than a public URL",async()=>{
    const fetch=mockFetch(true,{...empty,loaded:true});const create=vi.fn(()=>"blob:ozon");vi.stubGlobal("URL",Object.assign(URL,{createObjectURL:create,revokeObjectURL:vi.fn()}));vi.spyOn(HTMLAnchorElement.prototype,"click").mockImplementation(()=>{});
    render(<OzonAdsAnalyticsPanel {...props}/>);await screen.findByLabelText("ID кампаний Performance");await userEvent.click(screen.getByRole("button",{name:"Скачать CSV рекламы Ozon"}));
    await waitFor(()=>expect(create).toHaveBeenCalled());const call=fetch.mock.calls.find(([url])=>url.includes("/export.csv"));expect(call?.[1]?.headers).toBeDefined();
  });
  it("aborts stale period requests and ignores late old results",async()=>{
    let resolve:(value:unknown)=>void=()=>{};const pending=new Promise((done)=>{resolve=done;});
    const fetch=vi.fn(async(url:string)=>url.includes("start_date=2026-09-01")?pending:response(url.endsWith("/credentials")?{configured:false}:empty));vi.stubGlobal("fetch",fetch);
    const view=render(<OzonAdsAnalyticsPanel {...props}/>);view.rerender(<OzonAdsAnalyticsPanel {...props} start="2026-09-03" end="2026-09-04"/>);
    await screen.findByText(/не означает нулевой расход/);resolve(response({...empty,loaded:true,totals:{...totals,spend:999}}));
    await waitFor(()=>expect(screen.queryByText("999 ₽")).toBeNull());expect(screen.getByText(/2026-09-03 — 2026-09-04/)).toBeTruthy();
  });
});
