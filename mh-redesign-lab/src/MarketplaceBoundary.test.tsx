import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { MarketplaceBoundary, marketplaceSwitchUrl } from "./MarketplaceBoundary";
const context = vi.hoisted(() => ({ clear: vi.fn(), select: vi.fn() }));
vi.mock("./data-source",()=>({API_BASE_URL:""}));
vi.mock("./marketplace-context",()=>({ clearMarketplaceContext:context.clear, selectMarketplace:context.select, validateMarketplaceSession:(raw:unknown)=>raw }));
const session=(marketplace="wb")=>({marketplace,role:"owner",can_write:true,is_demo:false,authorized:true,shared_routes:[]});
beforeEach(()=>{localStorage.clear();history.replaceState(null,"","/?view=products&shop=2");context.clear.mockClear();context.select.mockClear();vi.stubGlobal("fetch",vi.fn().mockImplementation((input:unknown)=>Promise.resolve({ok:true,json:async()=>session(String(input).includes("marketplace=ozon")?"ozon":"wb")})));});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it("mounts exactly one marketplace after authorization and removes WB shop from Ozon URL",async()=>{
 render(<MarketplaceBoundary wb={<p>WB content</p>} ozon={()=> <p>Ozon content</p>}/>);
 await screen.findByText("WB content");fireEvent.click(screen.getByRole("button",{name:"Ozon"}));
 await screen.findByText("Ozon content");expect(screen.queryByText("WB content")).not.toBeInTheDocument();
 expect(new URL(location.href).searchParams.get("shop")).toBeNull();
 fireEvent.click(screen.getByRole("button",{name:"Wildberries"}));await screen.findByText("WB content");
 expect(new URL(location.href).searchParams.get("shop")).toBe("2");
});
it("does not mount Ozon when access is denied",async()=>{
 history.replaceState(null,"","/?marketplace=ozon");vi.mocked(fetch).mockResolvedValue({ok:false,json:async()=>({detail:{message:"Нет отдельного доступа Ozon"}})} as Response);
 render(<MarketplaceBoundary wb={<p>WB content</p>} ozon={()=> <p>Ozon content</p>}/>);
 expect(await screen.findByText("Нет отдельного доступа Ozon")).toBeInTheDocument();expect(screen.queryByText("Ozon content")).not.toBeInTheDocument();
});
it("a delayed previous context cannot remount the previous marketplace",async()=>{
 let resolve:(value:Response)=>void=()=>{};vi.mocked(fetch).mockReturnValueOnce(new Promise<Response>(r=>{resolve=r;}));
 render(<MarketplaceBoundary wb={<p>WB content</p>} ozon={()=> <p>Ozon content</p>}/>);
 await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(1));fireEvent.click(screen.getByRole("button",{name:"Ozon"}));await screen.findByText("Ozon content");
 resolve({ok:true,json:async()=>session("wb")} as Response);await waitFor(()=>expect(screen.queryByText("WB content")).not.toBeInTheDocument());
 expect(context.select.mock.calls.at(-1)?.[0].marketplace).toBe("ozon");
});
it("unknown URL selector fails closed until the user chooses a known marketplace",async()=>{
 history.replaceState(null,"","/?marketplace=other");render(<MarketplaceBoundary wb={<p>WB content</p>} ozon={()=> <p>Ozon content</p>}/>);
 await screen.findByText("Неизвестная площадка. Выберите WB или Ozon.");expect(fetch).not.toHaveBeenCalled();fireEvent.click(screen.getByRole("button",{name:"Wildberries"}));await screen.findByText("WB content");
});
it("switch preferences save only navigation fields and clear incompatible anchors",()=>{
 history.replaceState(null,"","/?view=finance&shop=2&secret=not-persisted#tax-reconciliation");
 const url=new URL(marketplaceSwitchUrl("ozon"));expect(url.hash).toBe("");expect(url.searchParams.get("shop")).toBeNull();expect(localStorage.getItem("mh-marketplace-view:wb")).not.toContain("secret");
});
