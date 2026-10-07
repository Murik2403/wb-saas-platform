import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {ShopWorkspaces} from './ShopWorkspaces';
import {selectMarketplace,clearMarketplaceContext} from './marketplace-context';
afterEach(()=>{cleanup();clearMarketplaceContext();vi.unstubAllGlobals();});
const listing={items:[{id:1,name:'Second',active:true,synced_at:'2026-09-30'}],can_manage:false,can_write:false};
describe('Shop workspaces',()=>{
 it.each(['manager','viewer','demo'])('does not trust a writable listing to grant connection controls to %s',async role=>{
  selectMarketplace({marketplace:'wb',role:role==='demo'?'owner':role,can_write:role==='manager',is_demo:role==='demo',authorized:true,shared_routes:[]});
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response(JSON.stringify({...listing,can_manage:true,can_write:true}))));
  render(<ShopWorkspaces/>);await screen.findByRole('option',{name:'Second'});
  expect(screen.queryByRole('button',{name:'Подключить магазин'})).toBeNull();
  expect(screen.queryByRole('button',{name:'Обновить подключение, сохранив данные'})).toBeNull();
  fireEvent.change(screen.getByLabelText('Дополнительный магазин'),{target:{value:'1'}});
  expect(screen.queryByRole('button',{name:'Отключить магазин, сохранив данные'})).toBeNull();
  if(role==='manager')expect(screen.getByRole('button',{name:'Загрузить историю за 30 дней'})).toBeTruthy();
  else expect(screen.queryByRole('button',{name:'Загрузить историю за 30 дней'})).toBeNull();
 });
 it('owner connection form also rechecks current permission before an old submit',async()=>{
  selectMarketplace({marketplace:'wb',role:'owner',can_write:true,is_demo:false,authorized:true,shared_routes:[]});
  const fetcher=vi.fn().mockImplementation(()=>Promise.resolve(new Response(JSON.stringify({...listing,can_manage:true,can_write:true}))));
  vi.stubGlobal('fetch',fetcher);render(<ShopWorkspaces/>);
  const connect=await screen.findByRole('button',{name:'Подключить магазин'});
  selectMarketplace({marketplace:'wb',role:'manager',can_write:true,is_demo:false,authorized:true,shared_routes:[]});
  fireEvent.submit(connect.closest('form')!);
  expect(fetcher.mock.calls.every(([,init])=>init.method==='GET')).toBe(true);
 });
 it('viewer can select and read without mutation controls',async()=>{
  vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,json:async()=>listing}));
  render(<ShopWorkspaces/>);
  await screen.findByRole('option',{name:'Second'});
  expect(screen.queryByRole('button',{name:'Подключить магазин'})).toBeNull();
  fireEvent.change(screen.getByLabelText('Дополнительный магазин'),{target:{value:'1'}});
  expect(screen.getByRole('button',{name:'Открыть отчёт магазина'})).toBeTruthy();
  expect(screen.queryByRole('button',{name:'Загрузить историю за 30 дней'})).toBeNull();
 });
 it('freezes selection and dates while a shop request is pending',async()=>{
  let finish:(v:unknown)=>void=()=>{};
  const fetcher=vi.fn().mockResolvedValueOnce({ok:true,json:async()=>listing}).mockImplementationOnce(()=>new Promise(r=>{finish=r;}));
  vi.stubGlobal('fetch',fetcher);render(<ShopWorkspaces/>);
  await screen.findByRole('option',{name:'Second'});
  fireEvent.change(screen.getByLabelText('Дополнительный магазин'),{target:{value:'1'}});
  fireEvent.click(screen.getByRole('button',{name:'Открыть отчёт магазина'}));
  await waitFor(()=>expect((screen.getByLabelText('Дополнительный магазин') as HTMLSelectElement).disabled).toBe(true));
  expect((screen.getByLabelText('Период с') as HTMLInputElement).disabled).toBe(true);
  finish({ok:false,json:async()=>({detail:'Ошибка загрузки'})});
  await screen.findByRole('alert');
  expect((screen.getByLabelText('Дополнительный магазин') as HTMLSelectElement).disabled).toBe(false);
 });
});
