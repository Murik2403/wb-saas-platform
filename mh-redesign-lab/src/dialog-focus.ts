import {useCallback,useLayoutEffect,useRef,type RefCallback} from 'react';

const activeScopes:HTMLElement[]=[];
function controls(root:HTMLElement){
 return Array.from(root.querySelectorAll<HTMLElement>('a[href],button,input,select,textarea,[tabindex]')).filter(element=>{
  if(element.tabIndex<0||element.matches(':disabled')||element.closest('[hidden],[inert]'))return false;
  for(let node:HTMLElement|null=element;node&&node!==root.parentElement;node=node.parentElement){const style=getComputedStyle(node);if(style.display==='none'||style.visibility==='hidden')return false;}
  return true;
 });
}
/** Presentation-only scope: operation/cancellation behavior remains in onClose. */
export function createDialogFocusScope(root:HTMLElement,onClose:()=>void,opener:HTMLElement|null=document.activeElement instanceof HTMLElement?document.activeElement:null){
 const oldTabIndex=root.getAttribute('tabindex');root.tabIndex=-1;
 activeScopes.push(root);
 const first=()=>controls(root)[0]??root;
 first().focus();
 const keydown=(event:KeyboardEvent)=>{
  if(activeScopes.at(-1)!==root||event.isComposing)return;
  const activeNative=Array.from(document.querySelectorAll<HTMLDialogElement>('dialog[open]')).at(-1);
  if(activeNative&&activeNative!==root&&!root.contains(activeNative))return;
  if(event.key==='Escape'){event.preventDefault();event.stopPropagation();onClose();return;}
  if(event.key!=='Tab')return;
  const items=controls(root),head=items[0]??root,tail=items.at(-1)??root,focused=document.activeElement;
  if(!items.length||!root.contains(focused)||(event.shiftKey?focused===head:focused===tail)){event.preventDefault();(event.shiftKey?tail:head).focus();}
 };
 document.addEventListener('keydown',keydown,true);
 return ()=>{
  document.removeEventListener('keydown',keydown,true);
  const wasTop=activeScopes.at(-1)===root,index=activeScopes.indexOf(root);if(index>=0)activeScopes.splice(index,1);
  if(oldTabIndex===null)root.removeAttribute('tabindex');else root.setAttribute('tabindex',oldTabIndex);
  if(wasTop&&opener?.isConnected&&(!activeScopes.length||activeScopes.at(-1)!.contains(opener)))opener.focus();
 };
}
export function useDialogFocus(onClose:()=>void):RefCallback<HTMLElement>{
 const opener=useRef<HTMLElement|null>(document.activeElement instanceof HTMLElement?document.activeElement:null);
 const node=useRef<HTMLElement|null>(null),close=useRef(onClose);close.current=onClose;
 const ref=useCallback((element:HTMLElement|null)=>{node.current=element;},[]);
 useLayoutEffect(()=>node.current?createDialogFocusScope(node.current,()=>close.current(),opener.current):undefined,[]);
 return ref;
}
