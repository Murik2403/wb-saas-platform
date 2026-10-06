import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { OzonProductInsights } from './OzonProductInsights';
import { scopedFetch } from './selected-shop';
vi.mock('./data-source',()=>({API_BASE_URL:'/tenant-api',apiHeaders:()=>({})}));
vi.mock('./selected-shop',()=>({scopedFetch:vi.fn()}));
const product={sku:'123',offer_id:'Synthetic',order_units:3,sales_units:null,return_units:null,revenue_rub:null,historical_cogs_rub:null,result_after_fifo_rub:null,unknown_cost_units:null,plan_units:null,fact_units:null,favorite:false,plan_days:[]};
const report={items:[product],blockers:['Коллекция не подтверждена'],loaded:true,truncated:false,coverage:{source_collection_complete:false,marketplace_complete:false},unallocated_document_fees_rub:null};
const response=(body:unknown)=>Promise.resolve({ok:true,json:async()=>body} as Response);
beforeEach(()=>{
  vi.resetAllMocks();
  vi.mocked(scopedFetch).mockImplementation(input=>response(String(input).includes('/heatmap?')?{dates:['2026-09-01'],items:[{sku:'123',values:[null]}],blockers:[],metric:'order_units',basis:'posting_created_at_utc',metric_note:'Созданные отправления; не продажи.',truncated:false}:report));
});

it('keeps missing documentary sales and money unknown and separates orders',async()=>{
  render(<OzonProductInsights start="2026-09-01" end="2026-09-30" canWrite={false}/>);
  const table=await screen.findByRole('region',{name:'Таблица товаров Ozon'});
  expect(within(table).getByText('3')).toBeInTheDocument();
  expect(within(table).getAllByText('не рассчитано')).toHaveLength(3);
  expect(screen.getByText(/Заказы — созданные отправления/)).toBeInTheDocument();
});

it('fetches heatmap only on selection and labels unknown cells',async()=>{
  render(<OzonProductInsights start="2026-09-01" end="2026-09-30" canWrite={false}/>);
  await screen.findByRole('region',{name:'Таблица товаров Ozon'});
  expect(scopedFetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button',{name:'Тепловая карта'}));
  const table=await screen.findByRole('region',{name:'Тепловая карта Ozon'});
  expect(within(table).getByTitle('Нет подтверждения')).toHaveTextContent('—');
  fireEvent.change(screen.getByLabelText('Метрика тепловой карты Ozon'),{target:{value:'document_revenue_rub'}});
  await waitFor(()=>expect(String(vi.mocked(scopedFetch).mock.calls.at(-1)?.[0])).toContain('metric=document_revenue_rub'));
});

it('saves only a local Ozon SKU plan and preserves confirmation after refresh',async()=>{
  render(<OzonProductInsights start="2026-09-01" end="2026-09-30" canWrite/>);
  await screen.findByRole('region',{name:'Таблица товаров Ozon'});
  fireEvent.click(screen.getByRole('button',{name:'План и факт'}));
  const input=await screen.findByLabelText('План SKU 123');
  fireEvent.change(input,{target:{value:'5'}});fireEvent.click(screen.getByRole('button',{name:'Сохранить'}));
  await screen.findByText('✓ Сохранено');
  const write=vi.mocked(scopedFetch).mock.calls.find(([,options])=>options?.method==='PUT');
  expect(String(write?.[0])).toContain('/ozon-insights/rnp/plan');
  expect(JSON.parse(String(write?.[1]?.body))).toEqual({namespace:'main',sku:'123',day:'2026-09-30',units:5});
});

it('viewer cannot submit plan or favorite and namespace applies once',async()=>{
  render(<OzonProductInsights start="2026-09-01" end="2026-09-30" canWrite={false}/>);
  await screen.findByRole('region',{name:'Таблица товаров Ozon'});
  fireEvent.change(screen.getByLabelText('Реестр аналитики Ozon'),{target:{value:'other'}});
  expect(scopedFetch).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button',{name:'Применить реестр'}));
  await waitFor(()=>expect(String(vi.mocked(scopedFetch).mock.calls.at(-1)?.[0])).toContain('namespace=other'));
  fireEvent.click(screen.getByRole('button',{name:'План и факт'}));
  expect(await screen.findByLabelText('План SKU 123')).toBeDisabled();
  expect(screen.getByRole('button',{name:'Избранное SKU 123'})).toBeDisabled();
  expect(screen.getByRole('button',{name:'Сохранить'})).toBeDisabled();
});

it('aborts stale write when the reporting period changes',async()=>{
  let finish:((value:Response)=>void)|undefined;
  vi.mocked(scopedFetch).mockImplementation((input,options)=>options?.method==='PUT'?new Promise< Response >(resolve=>{finish=resolve;}):response(report));
  const view=render(<OzonProductInsights start="2026-09-01" end="2026-09-30" canWrite/>);
  await screen.findByRole('region',{name:'Таблица товаров Ozon'});
  fireEvent.click(screen.getByRole('button',{name:'План и факт'}));
  await screen.findByLabelText('План SKU 123');
  fireEvent.click(screen.getByRole('button',{name:'Сохранить'}));
  const signal=vi.mocked(scopedFetch).mock.calls.find(([,options])=>options?.method==='PUT')?.[1]?.signal;
  view.rerender(<OzonProductInsights start="2026-10-01" end="2026-10-31" canWrite/>);
  expect(signal?.aborted).toBe(true);
  finish?.({ok:true,json:async()=>({saved:true})} as Response);
  await waitFor(()=>expect(screen.queryByText('✓ Сохранено')).not.toBeInTheDocument());
});
