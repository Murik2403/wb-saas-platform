import {cleanup,fireEvent,render,screen,waitFor} from '@testing-library/react';
import {afterEach,describe,expect,it,vi} from 'vitest';
import {ShopWorkspaces} from './ShopWorkspaces';
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
const listing={items:[{id:1,name:'Second',active:true,synced_at:'2026-09-30'}],can_manage:false,can_write:false};
describe('Shop workspaces',()=>{
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
