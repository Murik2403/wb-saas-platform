"""Create an offline training fixture using real current React components.

Only the generated fixture is mocked. Production sources and customer data are
never altered; all outgoing application fetches fail closed to local examples.
"""
from pathlib import Path
import argparse, json, shutil, subprocess

REPO = Path(__file__).resolve().parents[1]
parser=argparse.ArgumentParser();parser.add_argument('--output',type=Path,required=True)
args=parser.parse_args();base=args.output.resolve();base.mkdir(exist_ok=True,parents=True)
rows=json.loads((REPO/'docs/training/NEW_LESSONS_2026-10-03.json').read_text(encoding='utf8'))
(base/'CURRICULUM.json').write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding='utf8')
lab=REPO/'mh-redesign-lab'
entry=lab/'src/training-preview-private.tsx';page=lab/'training-preview-private.html'
fixture=r'''import React from "react";
import {createRoot} from "react-dom/client";
import {WorkspaceTabs} from "./WorkspaceTabs";
import {TaxReconciliationPanel} from "./TaxReconciliationPanel";
import {ProductsHeatmap} from "./ProductsHeatmap";
import {RnpPlanFact} from "./RnpPlanFact";
import {AdsAnalyticsPanel} from "./AdsAnalyticsPanel";
import {FbsOrdersPanel} from "./FbsOrdersPanel";
import {SelfBuyoutsPanel} from "./SelfBuyoutsPanel";
import "./styles.css";import "./CabinetUx.css";import "./Theme.css";
const period={id:"custom" as const,start:"2026-09-01",end:"2026-09-30"};
const products=[{nmId:"1001",article:"TRAIN-01",product:"Учебная салфетка"},{nmId:"1002",article:"TRAIN-02",product:"Учебная скатерть"}] as any;
const dates=["2026-09-01","2026-09-02","2026-09-03","2026-09-04"];
const m=(o={})=>({spend:550,views:3500,clicks:150,ctr:4.29,cpc:3.67,cpm:157.14,carts:30,cart_conversion:20,orders:15,order_conversion:10,cpo:36.67,ad_revenue:4000,drr:13.75,canceled:3,...o});
const ads={start:dates[0],end:dates[3],previous_start:"2026-08-28",previous_end:"2026-08-31",bucket_days:1,has_data:true,has_previous_data:true,totals:m(),previous_totals:m({spend:500,ctr:3.82,cpc:4,cpo:null}),by_day:dates.map((day,i)=>({day,...m({spend:100+i*25,orders:3+i})})),campaigns:[{campaign_id:77,...m()}]};
const heat={metric:"orders",label:"Заказы, шт",unit:"pcs",start:dates[0],end:dates[3],bucket_days:1,dates,rows:[{nm_id:1001,vendor_code:"TRAIN-01",name:"Учебная салфетка",total:18,values:[3,5,4,6]},{nm_id:1002,vendor_code:"TRAIN-02",name:"Учебная скатерть",total:11,values:[2,0,5,4]}],other:null,column_totals:[5,5,9,10],grand_total:29,min:0,max:6,campaign_level_total:null};
const rnpDay=(day:string,i:number)=>({day,is_future:i===3,is_today:i===2,plan_orders:5,fact_orders:i===3?null:3+i,fact_orders_sum:i===3?null:(3+i)*500,fact_sales:i===3?null:2+i,fact_sales_sum:i===3?null:(2+i)*500,forecast_sales:3,deviation_abs:i===3?null:i-2,deviation_pct:i===3?null:(i-2)*20,price_before:625,price_after:500,ad_spend:50,drr_pct:5,stock:null});
const rnp={start:dates[0],end:dates[3],today:dates[2],capped:false,max_days:31,max_favorites:10,forecast_note:"прогноз = среднее за последние 7 дней",favorites:[1001],items:[{nm_id:1001,vendor_code:"TRAIN-01",current_price:625,current_discount:20,current_final:500,stock_now:45,stock_snapshot_at:null,margin_status:"ok",margin_note:"Учебный пример",breakeven_final:350,days:dates.map(rnpDay),totals:{plan_orders:20,fact_orders:12,fact_orders_sum:6000,fact_sales:9,fact_sales_sum:4500,forecast_sales:12,deviation_abs:-8,deviation_pct:-40,ad_spend:200,drr_pct:4.4},plan_margin_pct:25,forecast_margin_pct:20,margin_gap_pp:-5}],ads_note:"Вымышленные значения"};
const fs={last_run_at:"2026-09-30T10:00:00Z",last_success_at:"2026-09-30T10:00:00Z",last_error:null,min_interval_seconds:300,retry_after_seconds:0,has_marketplace_scope:true,overdue_hours:24,orders_total:3};
const fo={items:[{order_id:7001,nm_id:1001,article:"TRAIN-01",created_at:"2026-09-29T08:00:00Z",price:50000,warehouse_id:7,supply_id:null,supplier_status:"new",wb_status:"waiting",state:"processing",state_label:"Сборка",age_hours:26,overdue:true},{order_id:7002,nm_id:1002,article:"TRAIN-02",created_at:"2026-09-30T08:00:00Z",price:75000,warehouse_id:7,supply_id:null,supplier_status:"complete",wb_status:"sold",state:"delivered",state_label:"Выкуплен",age_hours:2,overdue:false}],total:2,limit:50,offset:0,days:14,overdue_hours:24,warehouses:[{id:7,count:2}]};
const fa={days:14,total:3,by_status:[{key:"processing",label:"Сборка",count:1},{key:"delivered",label:"Выкуплен",count:1},{key:"cancelled",label:"Отменён",count:1}],final_count:2,buyout_pct:50,avg_delivery_hours:36,avg_delivery_sample:1,overdue_count:1,by_day:[{day:"2026-09-30",total:3,delivered:1,cancelled:1}],estimate_note:"Оценка по учебным статусам"};
let record={id:1,day:"2026-09-15",nm_id:1001,vendor_code:"TRAIN-01",units:2,buyout_amount_kop:100000,cashback_kop:10000,net_cost_kop:90000,format:"учебная запись",note:"Вымышленный документ",created_at:"",actor:"учебный владелец"};
const tot={records:1,units:2,paid_kop:100000,cashback_kop:10000,net_cost_kop:90000};
const buyouts={start:period.start,end:period.end,group:"day",nm_id:null,previous_window:{start:"2026-08-02",end:"2026-08-31"},totals:tot,previous_totals:{records:0,units:0,paid_kop:0,cashback_kop:0,net_cost_kop:0},deltas:Object.fromEntries(["units","paid_kop","cashback_kop","net_cost_kop"].map(k=>[k,{value:(tot as any)[k],previous:0,delta:(tot as any)[k],delta_pct:null}])),groups:[{start:record.day,...tot}],records:[record],truncated:false};
let tax:any={values:{month:"2026-09",regime:"ausn_income",period_mode:"month",rate:8,vat_mode:"exempt",taxpayer_type:"ip",has_staff:false,source:"Вымышленная выписка УЧ-01",confirmed:true,received:100000,offset:20000,compensation:0,other_income:0,refunds:5000,profit_before_tax:30000,fns_assessed:9200,paid:9200},revision:1,can_write:true,period:{start:"2026-09-01",end:"2026-09-30"},calculation:{status:"ready",missing:[],income:115000,base:115000,main_tax:9200,minimum_tax:0,estimate:9200,vat_payable:0,vat_credit:0,after_estimate:20800,fns_difference:0,after_fns:20800,assessed_minus_paid:0,negative_base:false},history:[]};
tax.history=[{revision:1,created_at:"2026-09-30T12:00:00",actor:"учебный владелец",values:tax.values,calculation:tax.calculation}];
globalThis.fetch=async(input:any,init:any={})=>{let url=String(input);let body:any={};
 if(url.includes("tax-reconciliation")){if(init.method==="PUT"){tax={...tax,values:JSON.parse(init.body),revision:tax.revision+1};}else{const requestedMonth=new URL(url,location.href).searchParams.get("month");if(requestedMonth)tax={...tax,values:{...tax.values,month:requestedMonth}};}body=tax;}
 else if(url.includes("/session"))body={user:{id:"1",name:"Учебный владелец",role:"owner"},tenant:{id:"training",name:"Учебный магазин"},mode:"live"};
 else if(url.includes("ads/analytics"))body=ads;
 else if(url.includes("heatmap")){const metric=new URL(url,location.origin).searchParams.get("metric")||"orders";body={...heat,metric,label:metric==="revenue"?"Выручка, ₽":"Заказы, шт",unit:metric==="revenue"?"rub":"pcs"};}
 else if(url.includes("dashboard/rnp"))body=rnp;
 else if(url.includes("/rnp/"))body={ok:true};
 else if(url.includes("/fbs/state")||url.includes("/fbs/refresh"))body=fs;
 else if(url.includes("/fbs/orders"))body=fo;
 else if(url.includes("/fbs/analytics"))body=fa;
 else if(url.includes("self-buyouts")){body=init.method==="POST"?record:{...buyouts,group:new URL(url,location.origin).searchParams.get("group")||"day"};}
 else throw new Error("Учебный контур: внешний запрос запрещён");
 return new Response(JSON.stringify(body),{status:200,headers:{"Content-Type":"application/json"}});
};
const view=new URLSearchParams(location.search).get("view")||"tax";
const title=({tax:"Финансы",products:"Товары",ads:"Реклама",fbs:"Поставки",buyouts:"Финансы"} as any)[view];
const finance=[{id:"overview",label:"Обзор"},{id:"money",label:"Счета и кассовые окна"},{id:"tax",label:"Налоги"},{id:"costs",label:"Расходы и себестоимость"},{id:"accounts",label:"Магазины и сценарии"}];
const panel=view==="tax"?<TaxReconciliationPanel/>:view==="products"?<><ProductsHeatmap period={period}/><RnpPlanFact products={products}/></>:view==="ads"?<AdsAnalyticsPanel period={period}/>:view==="fbs"?<FbsOrdersPanel canWrite/>:<SelfBuyoutsPanel role="owner" products={products} period={period} confirm={async()=>true}/>;
const tabs=view==="tax"||view==="buyouts"?finance:view==="products"?[{id:"products",label:"Товары"},{id:"analytics",label:"Аналитика"},{id:"content",label:"Контент и качество"},{id:"prices",label:"Цены"}]:view==="ads"?[{id:"campaigns",label:"Кампании"},{id:"analytics",label:"Аналитика РК"}]:[{id:"supplies",label:"Поставки WB"},{id:"fbs",label:"FBS-заказы"},{id:"purchase",label:"Закупки"}];
const selected=view==="tax"?"tax":view==="buyouts"?"costs":view==="fbs"?"fbs":"analytics";
document.documentElement.dataset.theme="dark";
createRoot(document.getElementById("root")!).render(<div style={{padding:32,maxWidth:1800,margin:"auto"}}><p style={{color:"#24dbb6",fontWeight:700}}>MARKETSHELPER · Учебный магазин · Вымышленные данные</p><h1>{title}</h1><WorkspaceTabs tabs={tabs} ariaLabel={`Разделы ${title}`} initial={selected} panels={Object.fromEntries(tabs.map(t=>[t.id,t.id===selected?panel:<p>Учебный маршрут: вернитесь к активной вкладке.</p>]))}/></div>);
'''
entry.write_text(fixture,encoding='utf8');page.write_text('<!doctype html><html lang="ru"><head><meta charset="utf-8"><title>MARKETSHELPER: учебный контур</title></head><body><div id="root"></div><script type="module" src="/src/training-preview-private.tsx"></script></body></html>',encoding='utf8')
(base/'FIXTURE_SOURCE.tsx').write_text(fixture,encoding='utf8')
print(json.dumps({'fixture':str(page),'output':str(base),'synthetic':True,'network':'all application fetches local mocks only'},ensure_ascii=False))
