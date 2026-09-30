import {API_BASE_URL,apiHeaders} from './data-source';
import type {CashEntry} from './cashflow-source';
export type CashAccount={id:number;name:string;currency:string;active:number;revision:number;legacy:number;opening_minor:number;actual_closing_minor:number;forecast_closing_minor:number;opening_confirmed:boolean;period_before_opening:boolean;overdue_minor:number;daily:Array<{date:string;actual_minor:number;forecast_minor:number}>;items:Array<CashEntry & {native_minor:number;source_kind:string;bank_reference?:string;bank_external_id?:string}>};
export type AccountsReport={accounts:CashAccount[];external_inflow_rub_minor:number;external_outflow_rub_minor:number;transfer_valuation_delta_rub_minor:number};
export type StatementPreview={fingerprint:string;created:number;settled:number;skipped:number;rows:Array<{external_id:string;date:string;direction:string;amount:string;currency:string;plan_id:number|null;action:string;reference:string;plan_reference?:string}>};
export async function cashAccountRequest<T>(path:string,method='GET',body?:unknown):Promise<T>{
  const response=await fetch(`${API_BASE_URL}/api/v1/cash-accounts${path}`,{method,headers:apiHeaders(body!==undefined),body:body===undefined?undefined:JSON.stringify(body)});
  if(!response.ok){const error=await response.json().catch(()=>({})) as {detail?:unknown};throw new Error(typeof error.detail==='string'?error.detail:'Не удалось обработать счета. Проверьте данные и доступ.');}
  return response.json() as Promise<T>;
}
export function statementBody(accountId:number,csv:string){return {account_id:accountId,csv};}
