import { API_BASE_URL, apiHeaders } from './data-source';
import { currentShop, scopedFetch } from './selected-shop';

export type WarehouseProduct={nm_id:number;chrt_id:number;product_name:string;supplier_article:string;variant_label:string};
export type WarehouseItem={id:string;nm_id:number;chrt_id:number;name:string;revision:number};
export type WarehouseReservation={id:string;status:string;revision:number;quantity:number;source_ref:string;document_date:string;external_nm_id:number;external_chrt_id:number;canonical_nm_id:number;canonical_chrt_id:number;item_name:string};
export type WarehouseOwnership={reservation_id:string;layer_id:number;shop_id:number;inbound_units:number;wb_units:number;inbound_cost_rub:number;wb_cost_rub:number};
export type WarehouseHold={id:string;reservation_id:string;layer_id:number;location:'inbound'|'wb';units:number;status:string;revision:number;proof:string};
export type WarehouseDocument={request_key:string;reservation_id:string;kind:string;source_ref:string;document_date:string;result:Record<string,unknown>};
export type WarehouseWorkspace={shop_id:number;needs_primary_binding:boolean;can_configure:boolean;can_write:boolean;products_truncated?:boolean;products:WarehouseProduct[];items:WarehouseItem[];mappings:Array<{id:string;external_nm_id:number;external_chrt_id:number;item_id:string}>;reservations:WarehouseReservation[];ownership:WarehouseOwnership[];documents:WarehouseDocument[];holds:WarehouseHold[]};
export type WarehousePreview={fingerprint?:string;missing_units?:number;quantity?:number;cost_rub?:number;layers?:Array<{layer_id:number;units:number;cost_rub:number}>;allocations?:Array<{layer_id:number;units:number;cost_rub:number}>;[key:string]:unknown};
const base='/api/v1/shared-warehouse';

export async function warehouseRequest<T>(path:string,body?:Record<string,unknown>,key?:string,signal?:AbortSignal):Promise<T>{
 const response=await scopedFetch(API_BASE_URL+base+path,{method:body===undefined?'GET':'POST',headers:{...apiHeaders(body!==undefined),...(key?{'X-MH-Request-Key':key}:{})},body:body===undefined?undefined:JSON.stringify(body),signal});
 const value=await response.json();
 if(!response.ok){const detail=value?.detail;throw new Error(typeof detail==='string'?detail:detail?.message??'Не удалось выполнить складскую операцию');}
 return value as T;
}
export function warehouseWorkspace(signal?:AbortSignal){const shop=currentShop();if(!shop)throw new Error('Магазин не подтверждён');return warehouseRequest<WarehouseWorkspace>(`/${shop.id}/workspace`,undefined,undefined,signal);}

/** A failed attempt is an immutable receipt retry. Changing any input creates a new key. */
export class WarehouseSubmission{
 private pending:{identity:string;key:string;body:Record<string,unknown>}|null=null;
 snapshot(path:string,body:Record<string,unknown>){
  const shop=currentShop();if(!shop)throw new Error('Магазин не подтверждён');
  const serialized=JSON.stringify(body);const identity=JSON.stringify([shop.id,shop.verified_identity,shop.snapshot_revision,path,serialized]);
  if(this.pending?.identity!==identity)this.pending={identity,key:crypto.randomUUID(),body:JSON.parse(serialized)};
  return {key:this.pending.key,body:structuredClone(this.pending.body)};
 }
 clear(){this.pending=null;}
}
