import {API_BASE_URL,apiHeaders} from './data-source';
import {currentShop,scopedFetch} from './selected-shop';
import {writeError} from './write-error';
export type ScheduleKind='sync'|'pdf';
export type ShopSchedule={id:number|null;shop_id:number;kind:ScheduleKind;enabled:boolean;exists:boolean;generation:number;interval_minutes:number;period_days:number;metrics:string[];next_due_utc:string|null;last_status?:string|null;last_error?:string|null;last_run_key?:string|null};
export type ScheduleRun={id:number;run_key:string;kind:ScheduleKind;due_utc:string;status:'ok'|'failed';error_code:string|null;artifact:{run_id:number;sha256:string;start:string;end:string}|null};
export type ScheduleListing={shop_id:number;can_configure:boolean;can_enable:boolean;schedules:ShopSchedule[];runs:ScheduleRun[];metrics:string[];delivery:'local_only'};
export type ScheduleInput={enabled:boolean;expected_generation:number;interval_minutes:number;period_days:number;metrics:string[]};
export const scheduleAction='PUT /api/v1/shop-schedules/{shop_id}/{kind}';
export function selectedScheduleShop(){const shop=currentShop();if(!shop||shop.id<=0)throw new Error('Выберите дополнительный WB-магазин');return shop;}
export async function scheduleRequest<T>(kind?:ScheduleKind,input?:ScheduleInput,key?:string,signal?:AbortSignal):Promise<T>{
 const shop=selectedScheduleShop();
 const response=await scopedFetch(`${API_BASE_URL}/api/v1/shop-schedules/${shop.id}${kind?'/'+kind:''}`,{method:input?'PUT':'GET',headers:{...apiHeaders(Boolean(input)),...(key?{'X-MH-Request-Key':key}:{})},body:input?JSON.stringify(input):undefined,signal});
 if(!response.ok)throw await writeError(response);
 return await response.json() as T;
}
export async function getShopSchedules(signal?:AbortSignal){
 const shop=selectedScheduleShop(),value=await scheduleRequest<ScheduleListing>(undefined,undefined,undefined,signal);
 if(value.shop_id!==shop.id||typeof value.can_configure!=='boolean'||typeof value.can_enable!=='boolean'||value.delivery!=='local_only'||!Array.isArray(value.schedules)||!Array.isArray(value.runs)||!Array.isArray(value.metrics)||value.schedules.length!==2||new Set(value.schedules.map(s=>s.kind)).size!==2||value.schedules.some(s=>!['sync','pdf'].includes(s.kind)||s.shop_id!==shop.id||typeof s.enabled!=='boolean'||typeof s.exists!=='boolean'||!Number.isSafeInteger(s.generation)||s.generation<0||!Number.isInteger(s.interval_minutes)||!Number.isInteger(s.period_days)||!Array.isArray(s.metrics)))throw new Error('Ответ расписания не соответствует выбранному магазину');
 if(value.metrics.length>50||new Set(value.metrics).size!==value.metrics.length||value.metrics.some(metric=>typeof metric!=='string'||!/^[a-z_]{1,64}$/.test(metric)))throw new Error('Список метрик расписания недоступен');
 if(value.runs.length>200||new Set(value.runs.map(run=>run.id)).size!==value.runs.length||value.runs.some(run=>!Number.isSafeInteger(run.id)||run.id<=0||!['sync','pdf'].includes(run.kind)||!['ok','failed'].includes(run.status)||typeof run.run_key!=='string'||typeof run.due_utc!=='string'||(run.error_code!==null&&typeof run.error_code!=='string')||(run.artifact!==null&&(!Number.isSafeInteger(run.artifact.run_id)||run.artifact.run_id<=0||typeof run.artifact.sha256!=='string'||!/^[a-f0-9]{64}$/.test(run.artifact.sha256)))))throw new Error('История расписания недоступна: проверьте ответ магазина');
 return value;
}
export class ScheduleSubmission{
 private pending:{identity:string;key:string;input:ScheduleInput}|null=null;
 snapshot(kind:ScheduleKind,input:ScheduleInput){
  const shop=selectedScheduleShop(),serialized=JSON.stringify(input),identity=JSON.stringify([shop.id,shop.verified_identity,kind,serialized]);
  if(this.pending?.identity!==identity)this.pending={identity,key:crypto.randomUUID(),input:JSON.parse(serialized)};
  return {key:this.pending.key,input:structuredClone(this.pending.input)};
 }
 clear(){this.pending=null;}
}
