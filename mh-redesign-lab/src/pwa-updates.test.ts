import {afterEach,beforeEach,expect,it,vi} from "vitest";
import {startPwaUpdates} from "./pwa-updates";
let cleanup:(()=>void)|undefined;
const fetcher=vi.fn();
let registration: EventTarget & {waiting: {postMessage:ReturnType<typeof vi.fn>}|null; installing:null};
let worker: EventTarget & {controller:object;register:ReturnType<typeof vi.fn>};
beforeEach(()=>{
 vi.useFakeTimers();document.body.innerHTML='<input value="unsaved draft" />';
 Object.defineProperty(document,"visibilityState",{value:"visible",configurable:true});
 registration=Object.assign(new EventTarget(),{waiting:{postMessage:vi.fn()},installing:null});
 worker=Object.assign(new EventTarget(),{controller:{},register:vi.fn().mockResolvedValue(registration)});
 vi.stubGlobal("navigator",{serviceWorker:worker});vi.stubGlobal("fetch",fetcher);
 fetcher.mockReset();fetcher.mockResolvedValue({ok:true,text:async()=>"a".repeat(40)});
});
afterEach(()=>{cleanup?.();cleanup=undefined;vi.useRealTimers();vi.unstubAllGlobals();});
async function settle(){await Promise.resolve();await Promise.resolve();await Promise.resolve();}
it("never activates or reloads until explicit action and preserves drafts",async()=>{
 const reload=vi.fn();cleanup=startPwaUpdates(reload);await settle();
 expect(document.querySelector('[role="status"]')).not.toBeNull();
 expect((document.querySelector("input") as HTMLInputElement).value).toBe("unsaved draft");
 worker.dispatchEvent(new Event("controllerchange"));expect(reload).not.toHaveBeenCalled();
 expect(registration.waiting!.postMessage).not.toHaveBeenCalled();
 (Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Обновить страницу")!).click();
 expect(registration.waiting!.postMessage).toHaveBeenCalledWith({type:"MH_ACTIVATE_UPDATE"});
 worker.dispatchEvent(new Event("controllerchange"));expect(reload).toHaveBeenCalledTimes(1);
});
it("postpone and cleanup do not reload or activate",async()=>{
 const reload=vi.fn();cleanup=startPwaUpdates(reload);await settle();
 (Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Позже")!).click();
 expect(document.querySelector('[role="status"]')).toBeNull();
 cleanup();cleanup=undefined;worker.dispatchEvent(new Event("controllerchange"));
 expect(reload).not.toHaveBeenCalled();expect(registration.waiting!.postMessage).not.toHaveBeenCalled();
});
it("detects committed build updates even if sw.js remains unchanged, without reload",async()=>{
 registration.waiting=null;const reload=vi.fn();cleanup=startPwaUpdates(reload);await settle();
 expect(document.querySelector('[role="status"]')).toBeNull();
 fetcher.mockResolvedValue({ok:true,text:async()=>"b".repeat(40)});
 await vi.advanceTimersByTimeAsync(300000);await settle();
 expect(document.querySelector('[role="status"]')).not.toBeNull();expect(reload).not.toHaveBeenCalled();
 (Array.from(document.querySelectorAll("button")).find(b=>b.textContent==="Обновить страницу")!).click();
 expect(reload).toHaveBeenCalledTimes(1);
});
it("offline or HTML authentication response is not treated as a version",async()=>{
 registration.waiting=null;fetcher.mockResolvedValue({ok:true,text:async()=>"<html>Login</html>"});
 cleanup=startPwaUpdates(vi.fn());await settle();
 expect(document.querySelector('[role="status"]')).toBeNull();
 fetcher.mockRejectedValue(new Error("offline"));await vi.advanceTimersByTimeAsync(300000);
 expect(document.querySelector('[role="status"]')).toBeNull();
});
it("does not prompt on first installation without an existing controller",async()=>{
 worker.controller=null as unknown as object;cleanup=startPwaUpdates(vi.fn());await settle();
 expect(document.querySelector('[role="status"]')).toBeNull();expect(registration.waiting!.postMessage).not.toHaveBeenCalled();
});
