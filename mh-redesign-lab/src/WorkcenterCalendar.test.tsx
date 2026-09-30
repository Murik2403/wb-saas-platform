import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { WorkcenterCalendar } from "./WorkcenterCalendar";
vi.mock("./data-source",()=>({API_BASE_URL:"/tenant-api",apiHeaders:()=>({})}));
const fetchMock=vi.fn<typeof fetch>();
const data={centers:[{id:1,name:"Цех",daily_minutes:30,workdays_json:"[0,1,2,3,4]",revision:1}],norms:[],tasks:[{task_key:"task",nm_id:10,chrt_id:100,status:"Не начато",actual_units:0}],assignments:[],calendar:[],warnings:[{code:"unassigned_task",task_key:"task"}],history:[],read_only:false,can_schedule:true};
const json=(v:unknown,status=200)=>new Response(JSON.stringify(v),{status});
beforeEach(()=>{fetchMock.mockReset().mockImplementation(async()=>json(data));vi.stubGlobal("fetch",fetchMock);});
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
it("does not save a reschedule until explicit confirmation",async()=>{
 fetchMock.mockImplementation(async(url)=>String(url).endsWith("/preview")?json({preview_fingerprint:"a".repeat(64),calendar:[{date:"2026-09-30",center_id:1,center_name:"Цех",available_minutes:30,planned_minutes:50,overload_minutes:20,load_complete:true}],warnings:[]}):String(url).endsWith("/confirm")?json({replayed:false}):json(data));
 render(<WorkcenterCalendar/>);await screen.findByLabelText("Задание операции");
 fireEvent.change(screen.getByLabelText("Задание операции"),{target:{value:"task"}});fireEvent.change(screen.getByLabelText("Название операции"),{target:{value:"Пошив"}});fireEvent.change(screen.getByLabelText("Центр операции"),{target:{value:"1"}});
 fireEvent.click(screen.getByText("Проверить назначение"));await screen.findByText(/Изменения ещё не сохранены/);
 expect(fetchMock.mock.calls.filter(([url])=>String(url).endsWith("/confirm"))).toHaveLength(0);
 expect(screen.getByText("20")).toBeInTheDocument();expect(screen.getByLabelText("Начало календаря")).toBeDisabled();
 fireEvent.click(screen.getByText("Подтвердить назначение"));await screen.findByText("Назначение сохранено в истории");
 expect(fetchMock.mock.calls.filter(([url])=>String(url).endsWith("/confirm"))).toHaveLength(1);
});
it("shows incomplete norms and read-only planning",async()=>{
 fetchMock.mockResolvedValue(json({...data,read_only:true,can_schedule:false,calendar:[{date:"2026-09-30",center_id:1,center_name:"Цех",available_minutes:30,planned_minutes:0,overload_minutes:0,load_complete:false}],warnings:[{code:"missing_norm",task_key:"task",operation:"Пошив"}]}));
 render(<WorkcenterCalendar/>);await screen.findByText(/неполные нормы/);
 expect(screen.getByText("task: нет нормы операции Пошив")).toBeInTheDocument();expect(screen.getByText("Проверить назначение")).toBeDisabled();
});
