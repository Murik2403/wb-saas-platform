import {useCallback,useLayoutEffect,useRef,type RefCallback} from 'react';

const activeScopes:HTMLElement[]=[];
const background=new Map<Element,string|null>();
let savedScrollStyles:{html:string;body:string}|null=null;
function updateScrollLock(){
 if(activeScopes.length){
  if(!savedScrollStyles)savedScrollStyles={html:document.documentElement.style.overflow,body:document.body.style.overflow};
  document.documentElement.style.overflow='hidden';document.body.style.overflow='hidden';
 }else if(savedScrollStyles){
  document.documentElement.style.overflow=savedScrollStyles.html;document.body.style.overflow=savedScrollStyles.body;savedScrollStyles=null;
 }
}
function isolateTopScope(){
 for(const[element,value]of background){if(value===null)element.removeAttribute('inert');else element.setAttribute('inert',value);}
 background.clear();
 const root=activeScopes.at(-1);if(!root)return;
 for(let branch:Element=root;branch.parentElement;branch=branch.parentElement){
  for(const sibling of Array.from(branch.parentElement.children))if(sibling!==branch){background.set(sibling,sibling.getAttribute('inert'));sibling.setAttribute('inert','');}
  if(branch.parentElement===document.body)break;
 }
}
function controls(root:HTMLElement){
 return Array.from(root.querySelectorAll<HTMLElement>('a[href],button,input,select,textarea,summary,[tabindex],[contenteditable="true"]')).filter(element=>{
  const summary=element.matches('summary')&&element.parentElement?.matches('details')&&element.parentElement.querySelector('summary')===element;
  if((element.tabIndex<0&&(element.hasAttribute('tabindex')||(!summary&&!element.matches('[contenteditable="true"]'))))||element.matches(':disabled')||element.closest('[hidden],[inert]'))return false;
  for(let ancestor=element.parentElement;ancestor&&root.contains(ancestor);ancestor=ancestor.parentElement){
   if(ancestor.matches('details:not([open])')&&!ancestor.querySelector('summary')?.contains(element))return false;
  }
  for(let node:HTMLElement|null=element;node&&node!==root.parentElement;node=node.parentElement){const style=getComputedStyle(node);if(style.display==='none'||style.visibility==='hidden')return false;}
  return true;
 });
}
/** Presentation-only scope: operation/cancellation behavior remains in onClose. */
export function createDialogFocusScope(root:HTMLElement,onClose:()=>void,opener:HTMLElement|null=document.activeElement instanceof HTMLElement?document.activeElement:null){
 const oldTabIndex=root.getAttribute('tabindex');root.tabIndex=-1;
 // aria-modal describes a modal; inert actually removes the background from
 // keyboard, pointer and assistive-technology interaction. Preserve nested scopes.
 if(!activeScopes.length&&window.matchMedia?.('(max-width:1120px)').matches)document.documentElement.style.overflow='hidden';
 activeScopes.push(root);
 updateScrollLock();
 isolateTopScope();
 const first=()=>controls(root)[0]??root;
 first().focus();
 const separateNative=()=>Array.from(document.querySelectorAll<HTMLDialogElement>('dialog[open]')).some(dialog=>dialog!==root&&!root.contains(dialog));
 const focusin=(event:FocusEvent)=>{
  if(activeScopes.at(-1)===root&&!root.contains(event.target as Node)&&!separateNative())first().focus();
 };
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
 document.addEventListener('focusin',focusin,true);
 return ()=>{
  document.removeEventListener('keydown',keydown,true);
  document.removeEventListener('focusin',focusin,true);
  const wasTop=activeScopes.at(-1)===root,index=activeScopes.indexOf(root);if(index>=0)activeScopes.splice(index,1);
  updateScrollLock();
  if(oldTabIndex===null)root.removeAttribute('tabindex');else root.setAttribute('tabindex',oldTabIndex);
  isolateTopScope();
  if(!activeScopes.length)document.documentElement.style.overflow='';
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
