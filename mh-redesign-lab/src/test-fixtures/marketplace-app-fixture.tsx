/** App integration suites retain the real async marketplace boundary and their existing source mocks. */
import type { ReactElement } from "react";
import { act,render } from "@testing-library/react";
import { clearMarketplaceContext } from "../marketplace-context";
export async function renderAuthorizedWb(element:ReactElement){
 clearMarketplaceContext();
 try{window.localStorage.removeItem("mh-marketplace");}catch{/* storage failure has its own suites */}
 const source=globalThis.fetch;
 globalThis.fetch=(async(input:RequestInfo|URL,init?:RequestInit)=>{
  if(String(input).includes("/api/v1/marketplace-context/session?marketplace=wb"))return new Response(JSON.stringify({marketplace:"wb",role:"owner",can_write:true,is_demo:false,authorized:true,shared_routes:[]}),{status:200,headers:{"Content-Type":"application/json"}});
  return source(input,init);
 }) as typeof fetch;
 let result!:ReturnType<typeof render>;
 try{await act(async()=>{result=render(element);});return result;}finally{globalThis.fetch=source;}
}
