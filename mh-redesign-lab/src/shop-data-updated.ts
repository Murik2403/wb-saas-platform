import {currentShop,type SelectedShop} from './selected-shop';
export const shopDataUpdated='mh-shop-data-updated';
export type MutationScope=Pick<SelectedShop,'id'|'verified_identity'>;
export function notifyShopDataUpdated(scope:MutationScope|null){
 const current=currentShop();
 if(!scope||!current||scope.id!==current.id||scope.verified_identity!==current.verified_identity)return;
 window.dispatchEvent(new CustomEvent(shopDataUpdated,{detail:{shop_id:scope.id,identity:scope.verified_identity}}));
}
export function currentMutationEvent(event:Event){
 const scope=(event as CustomEvent).detail,current=currentShop();
 return Boolean(current&&scope&&scope.shop_id===current.id&&scope.identity===current.verified_identity);
}
