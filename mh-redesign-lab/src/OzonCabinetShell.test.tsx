import { beforeEach, afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import "@testing-library/jest-dom/vitest";
import { OzonCabinetShell,ozonPeriod } from "./OzonCabinetShell";
vi.mock("./OzonCabinetViews",()=>({OzonCabinetViews:(props:{page:string;start:string;end:string;canWrite:boolean})=><p data-testid="ozon-page">{JSON.stringify(props)}</p>}));
const session={marketplace:"ozon" as const,role:"owner",can_write:true,is_demo:false,authorized:true,shared_routes:[]};
beforeEach(()=>{history.replaceState(null,"","/?marketplace=ozon&view=products&period=custom&start=2026-09-01&end=2026-09-30");});
afterEach(cleanup);
it("all eleven destinations stay in the Ozon cabinet without a WB shop selector",async()=>{
 render(<OzonCabinetShell session={session}/>);expect(await screen.findByTestId("ozon-page")).toHaveTextContent('"page":"products"');
 for(const [label,page] of [["Сегодня","today"],["Склад","stock"],["Реклама","ads"],["Финансы","finance"],["Производство","production"],["Поставки","supply"],["Агенты","agents"],["Отчеты","reports"],["Товары","products"],["Контроль","control"],["Настройки","settings"]]){
  fireEvent.click(screen.getByRole("button",{name:label}));expect(screen.getByTestId("ozon-page")).toHaveTextContent(`"page":"${page}"`);
 }
 expect(new URL(location.href).searchParams.get("marketplace")).toBe("ozon");expect(screen.queryByLabelText("Магазин кабинета")).not.toBeInTheDocument();
});
it("demo cannot edit even when role is owner",()=>{
 render(<OzonCabinetShell session={{...session,is_demo:true}}/>);expect(screen.getByTestId("ozon-page")).toHaveTextContent('"canWrite":false');expect(screen.getByTestId("ozon-page")).toHaveTextContent('"isOwner":false');
});
it.each([['manager',true],['viewer',false]] as const)('keeps %s operational capability separate from connection ownership', (role,canWrite)=>{
 render(<OzonCabinetShell session={{...session,role,can_write:canWrite}}/>);
 expect(screen.getByTestId('ozon-page')).toHaveTextContent(`"canWrite":${canWrite}`);
 expect(screen.getByTestId('ozon-page')).toHaveTextContent('"isOwner":false');
});
it("invalid long date ranges never become active or trigger new report requests",()=>{
 render(<OzonCabinetShell session={session}/>);fireEvent.change(screen.getByLabelText("Ozon: начало периода"),{target:{value:"2025-01-01"}});fireEvent.click(screen.getByText("Применить период"));expect(screen.getByRole("alert")).toHaveTextContent("от 1 до 31 дня");expect(screen.getByTestId("ozon-page")).toHaveTextContent('"start":"2026-09-01"');
});
it("Moscow midnight and malformed calendar URLs produce a valid completed period",()=>{
 vi.useFakeTimers();vi.setSystemTime(new Date('2026-10-06T22:00:00Z'));
 expect(ozonPeriod(7)).toEqual({start:'2026-09-30',end:'2026-10-06'});
 history.replaceState(null,'','/?marketplace=ozon&start=2026-02-30&end=2026-03-01');
 render(<OzonCabinetShell session={session}/>);expect(screen.getByTestId('ozon-page')).toHaveTextContent('"start":"2026-09-07"');vi.useRealTimers();
});
it("mobile navigation supports Escape and restores keyboard focus",()=>{
 render(<OzonCabinetShell session={session}/>);const opener=screen.getByRole('button',{name:'Открыть меню Ozon'});opener.focus();fireEvent.click(opener);expect(screen.getByRole('dialog')).toBeInTheDocument();fireEvent.keyDown(document,{key:'Escape'});expect(screen.queryByRole('dialog')).not.toBeInTheDocument();expect(opener).toHaveFocus();
});
