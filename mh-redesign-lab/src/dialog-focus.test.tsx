import {useState} from 'react';
import {render,screen,fireEvent,cleanup} from '@testing-library/react';
import {afterEach,it,expect,vi} from 'vitest';
import '@testing-library/jest-dom/vitest';
import {createDialogFocusScope,useDialogFocus} from './dialog-focus';
afterEach(cleanup);
it('locks the document scroll until the last nested scope closes and restores inline styles',()=>{
 const html=document.documentElement,body=document.body;
 const originalHtml=html.style.overflow,originalBody=body.style.overflow;
 html.style.overflow='scroll';body.style.overflow='clip';
 const parent=document.createElement('section'),child=document.createElement('section');document.body.append(parent,child);
 const closeParent=createDialogFocusScope(parent,vi.fn()),closeChild=createDialogFocusScope(child,vi.fn());
 expect(html.style.overflow).toBe('hidden');expect(body.style.overflow).toBe('hidden');
 closeParent();expect(html.style.overflow).toBe('hidden');
 closeChild();expect(html.style.overflow).toBe('scroll');expect(body.style.overflow).toBe('clip');
 parent.remove();child.remove();html.style.overflow=originalHtml;body.style.overflow=originalBody;
});
it('activates a pre-existing sibling native dialog and preserves isolation on out-of-order disposal',()=>{
 const background=document.createElement('button'),parent=document.createElement('section'),child=document.createElement('dialog');parent.innerHTML='<button>Parent</button>';child.innerHTML='<button>Native</button>';document.body.append(background,parent,child);
 const disposeParent=createDialogFocusScope(parent,vi.fn());expect(child).toHaveAttribute('inert');child.open=true;
 const disposeChild=createDialogFocusScope(child,vi.fn());expect(child).not.toHaveAttribute('inert');expect(child.querySelector('button')).toHaveFocus();expect(parent).toHaveAttribute('inert');
 disposeParent();expect(background).toHaveAttribute('inert');expect(child).not.toHaveAttribute('inert');expect(child.querySelector('button')).toHaveFocus();
 disposeChild();expect(background).not.toHaveAttribute('inert');background.remove();parent.remove();child.remove();
});
it('wraps to a details summary while skipping controls in its collapsed content',()=>{
 const root=document.createElement('section');root.innerHTML='<button>Close</button><details><summary>Details</summary><input aria-label="Collapsed"/></details>';document.body.append(root);
 const dispose=createDialogFocusScope(root,vi.fn());
 fireEvent.keyDown(document,{key:'Tab',shiftKey:true});expect(root.querySelector('summary')).toHaveFocus();
 fireEvent.keyDown(document,{key:'Tab'});expect(root.querySelector('button')).toHaveFocus();dispose();root.remove();
});
it('makes the background inert and restores its previous state after closing',()=>{
 const page=document.createElement('main');page.innerHTML='<button>Background</button>';
 const existing=document.createElement('aside');existing.setAttribute('inert','');
 const backdrop=document.createElement('div'),root=document.createElement('section');root.innerHTML='<button>Close</button>';backdrop.append(root);document.body.append(page,existing,backdrop);
 const dispose=createDialogFocusScope(root,vi.fn());
 expect(page).toHaveAttribute('inert');expect(backdrop).not.toHaveAttribute('inert');expect(root).not.toHaveAttribute('inert');
 dispose();expect(page).not.toHaveAttribute('inert');expect(existing).toHaveAttribute('inert');page.remove();existing.remove();backdrop.remove();
});
it('recovers focus moved outside the dialog and keeps a nested modal isolated',()=>{
 const background=document.createElement('button');document.body.append(background);
 const parent=document.createElement('section');parent.innerHTML='<button>Parent</button>';document.body.append(parent);
 const disposeParent=createDialogFocusScope(parent,vi.fn());
 background.focus();expect(parent.querySelector('button')).toHaveFocus();
 const child=document.createElement('section');child.innerHTML='<button>Child</button>';document.body.append(child);const disposeChild=createDialogFocusScope(child,vi.fn());
 expect(parent).toHaveAttribute('inert');disposeChild();child.remove();expect(parent).not.toHaveAttribute('inert');expect(parent.querySelector('button')).toHaveFocus();
 disposeParent();parent.remove();background.remove();
});
function Drawer({close}:{close:()=>void}){const ref=useDialogFocus(close);return <section role="dialog" aria-modal="true" aria-label="Проверка" ref={ref}><button onClick={close}>Закрыть</button><input aria-label="Комментарий"/><button disabled>Недоступно</button><button hidden>Скрыто</button><button>Последняя</button></section>;}
function Harness(){const[open,setOpen]=useState(false);return <><button onClick={()=>setOpen(true)}>Открыть</button><button>Фон</button>{open&&<Drawer close={()=>setOpen(false)}/>}</>;}
it('enters dialog, confines both Tab boundaries and restores opener after Escape',()=>{render(<Harness/>);const opener=screen.getByText('Открыть');opener.focus();fireEvent.click(opener);expect(screen.getByText('Закрыть')).toHaveFocus();fireEvent.keyDown(document,{key:'Tab',shiftKey:true});expect(screen.getByText('Последняя')).toHaveFocus();fireEvent.keyDown(document,{key:'Tab'});expect(screen.getByText('Закрыть')).toHaveFocus();fireEvent.keyDown(document,{key:'Escape'});expect(screen.queryByRole('dialog')).not.toBeInTheDocument();expect(opener).toHaveFocus();});
it('handles a no-control dialog without letting Tab leave',()=>{const root=document.createElement('section');document.body.append(root);const close=vi.fn(),dispose=createDialogFocusScope(root,close);expect(root).toHaveFocus();fireEvent.keyDown(document,{key:'Tab'});expect(root).toHaveFocus();fireEvent.keyDown(document,{key:'Escape'});expect(close).toHaveBeenCalledOnce();dispose();root.remove();});
it('only closes the top nested scope and returns to the parent control',()=>{const parent=document.createElement('section');parent.innerHTML='<button>Parent</button>';document.body.append(parent);const closeParent=vi.fn(),disposeParent=createDialogFocusScope(parent,closeParent);const child=document.createElement('section');child.innerHTML='<button>Child</button>';document.body.append(child);const closeChild=vi.fn(),disposeChild=createDialogFocusScope(child,closeChild);fireEvent.keyDown(document,{key:'Escape'});expect(closeChild).toHaveBeenCalledOnce();expect(closeParent).not.toHaveBeenCalled();disposeChild();child.remove();expect(parent.querySelector('button')).toHaveFocus();disposeParent();parent.remove();});
it('does not intercept a separate native modal or restore a removed opener',()=>{const opener=document.createElement('button');document.body.append(opener);opener.focus();const root=document.createElement('section');root.innerHTML='<button>Close</button>';document.body.append(root);const close=vi.fn(),dispose=createDialogFocusScope(root,close);const native=document.createElement('dialog');native.open=true;document.body.append(native);fireEvent.keyDown(document,{key:'Escape'});expect(close).not.toHaveBeenCalled();native.remove();opener.remove();dispose();root.remove();});

function AutoFocusDrawer({busy,close}:{busy:boolean;close:()=>void}){const ref=useDialogFocus(()=>{if(!busy)close();});return <section role="dialog" aria-label="Auto focus" ref={ref}><input autoFocus aria-label="Price"/><button disabled={busy} onClick={close}>Cancel</button></section>;}
function AutoFocusHarness(){const[open,setOpen]=useState(false),[busy,setBusy]=useState(true);return <><button onClick={()=>setOpen(true)}>Open price</button><button onClick={()=>setBusy(false)}>Finish</button>{open&&<AutoFocusDrawer busy={busy} close={()=>setOpen(false)}/>}</>;}
it('captures external opener before React autoFocus and respects pending cancellation',()=>{render(<AutoFocusHarness/>);const opener=screen.getByText('Open price');opener.focus();fireEvent.click(opener);expect(screen.getByLabelText('Price')).toHaveFocus();fireEvent.keyDown(document,{key:'Escape'});expect(screen.getByRole('dialog')).toBeInTheDocument();fireEvent.click(screen.getByText('Finish'));fireEvent.keyDown(document,{key:'Escape'});expect(screen.queryByRole('dialog')).not.toBeInTheDocument();expect(opener).toHaveFocus();});
