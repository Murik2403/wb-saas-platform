import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import vm from "node:vm";
const source=readFileSync(new URL("../public/sw.js",import.meta.url),"utf8");
function runtime(response={ok:true,type:"basic",clone(){return this;}}){
 const handlers={};const puts=[];const deletes=[];let skipped=0;let entries=[];
 const cache={put:async(...args)=>{puts.push(args);},match:async()=>undefined,keys:async()=>entries,delete:async(key)=>{deletes.push(key);}};
 const caches={open:async()=>cache,keys:async()=>["other-app-private","marketshelper-shell-v1","marketshelper-shell-v2","marketshelper-shell-v3"],delete:async(key)=>{deletes.push(key);}};
 const self={location:{origin:"https://demo.app.marketshelper.ru"},clients:{claim:async()=>{}},skipWaiting:()=>{skipped++;},addEventListener:(kind,fn)=>{handlers[kind]=fn;}};
 const context=vm.createContext({self,caches,fetch:async()=>response,URL,Promise});vm.runInContext(source,context);
 return {handlers,puts,deletes,context,skip:()=>skipped,setEntries:value=>{entries=value;}};
}
async function request(runtime,path,{origin="https://demo.app.marketshelper.ru",method="GET",mode="cors"}={}){
 let result;const waits=[];
 runtime.handlers.fetch({request:{url:origin+path,method,mode},respondWith:p=>{result=p;},waitUntil:p=>waits.push(p)});
 if(result)await result;await Promise.all(waits);return result;
}
for(const path of ["/api/v1/download.png","/tenant-api/report.js","/reports/private.png","/assets/app.js?secret=x"]){const r=runtime();assert.equal(await request(r,path),undefined);assert.equal(r.puts.length,0);}
{const r=runtime();assert.equal(await request(r,"/assets/app.js",{origin:"https://foreign.example"}),undefined);assert.equal(await request(r,"/assets/app.js",{method:"POST"}),undefined);}
for(const response of [{ok:false,type:"basic",clone(){return this;}},{ok:true,type:"opaque",clone(){return this;}},{ok:true,type:"basic",redirected:true,clone(){return this;}},{ok:true,type:"basic",headers:{get:()=>"private, no-store"},clone(){return this;}}]){const r=runtime(response);await request(r,"/assets/app.js");assert.equal(r.puts.length,0);}
{const r=runtime();await request(r,"/assets/app.js");assert.equal(r.puts.length,1);assert.equal(r.skip(),0);r.handlers.message({data:{type:"MH_ACTIVATE_UPDATE"}});assert.equal(r.skip(),1);}
{const r=runtime();let activation;r.handlers.activate({waitUntil:p=>{activation=p;}});await activation;assert.deepEqual(r.deletes,["marketshelper-shell-v1"]);assert.equal(r.skip(),0);}
{const r=runtime();let installation;r.handlers.install({waitUntil:p=>{installation=p;}});await installation;assert.equal(r.puts.length,5);assert.equal(r.skip(),0);}
{const r=runtime();r.setEntries(Array.from({length:70},(_,i)=>({url:`https://demo.app.marketshelper.ru/assets/${i}.js`})));await r.context.storeStatic({url:"https://demo.app.marketshelper.ru/assets/new.js"},{ok:true,type:"basic"});assert.equal(r.deletes.length,6);}
console.log("PWA service-worker checks passed: private/API exclusions, basic+ok cache, explicit activation, scoped cleanup, bounded assets.");
