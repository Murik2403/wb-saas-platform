import { afterEach,describe,expect,it,vi } from 'vitest';
import { render,screen,fireEvent,waitFor,cleanup } from '@testing-library/react';
import '@testing-library/jest-dom/vitest';
import { OzonRealizationSource } from './OzonRealizationSource';
vi.mock('./data-source',()=>({API_BASE_URL:'',apiHeaders:()=>({})}));
vi.mock('./selected-shop',()=>({scopedFetch:(...args:Parameters<typeof fetch>)=>globalThis.fetch(...args)}));
afterEach(()=>{cleanup();vi.unstubAllGlobals();});
const report={loaded:true,document_number:'synthetic-report',document_date:'2026-10-01',source_version:'source-v1',source_row_count:2,period:{start_date:'2026-09-10',end_date:'2026-09-20'},partial_period:true,current_month_provisional:true,rows:[
  {source_occurrence_key:'a',row_number_text:'1',posting_number:'synthetic-order',sku_text:'123',delivery_commission:{quantity_text:'0',total_rub:'99999999999999999.99'},return_commission:{quantity_text:null,total_rub:'-25.01'},reason:'Количество возврата неизвестно'},
  {source_occurrence_key:'b',row_number_text:'1',posting_number:'synthetic-order',sku_text:'123',delivery_commission:{quantity_text:'2',total_rub:'100.01'},return_commission:{quantity_text:'1',total_rub:'-25.01'},reason:null}
]};
const response=(body:unknown,ok=true)=>({ok,status:ok?200:403,json:async()=>body}) as Response;
describe('Ozon monthly documentary source',()=>{
  it('reads local only until explicit load and preserves duplicate observations and exact money',async()=>{
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response({loaded:false,rows:[]})));
    render(<OzonRealizationSource canWrite/>);
    await screen.findByText(/Сохранённого месячного документа нет/);
    expect(vi.mocked(fetch).mock.calls.every(c=>c[1]?.method==='GET')).toBe(true);
    vi.mocked(fetch).mockResolvedValue(response(report));
    fireEvent.click(screen.getByRole('button',{name:'Загрузить месячный документ Ozon'}));
    await screen.findByText(/Документ: synthetic-report/);
    expect(screen.getAllByText('synthetic-order')).toHaveLength(2);
    expect(screen.getByText('0')).toBeInTheDocument();
    expect(screen.getByText('не указано / не распознано')).toBeInTheDocument();
    expect(screen.getByText('99 999 999 999 999 999,99 ₽')).toBeInTheDocument();
    expect(screen.getAllByText('-25,01 ₽')).toHaveLength(2);
    expect(screen.getByText(/Текущий месяц: документ предварительный/)).toBeInTheDocument();
    expect(screen.getByText(/Период исходного документа: 2026-09-10 — 2026-09-20/)).toBeInTheDocument();
    expect(screen.getByText(/Документ охватывает часть выбранного месяца/)).toBeInTheDocument();
    expect(screen.getByText(/не прибавляются к дневным начислениям/)).toBeInTheDocument();
    expect(screen.queryByRole('button',{name:/Подтвердить/})).not.toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.filter(c=>c[1]?.method==='POST')).toHaveLength(1);
  });
  it('keeps the saved report visible after upstream failure without retry storm',async()=>{
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response(report)));
    render(<OzonRealizationSource canWrite/>);await screen.findByText(/Документ: synthetic-report/);
    vi.mocked(fetch).mockResolvedValue(response({detail:'Ozon отказал в доступе'},false));
    fireEvent.click(screen.getByRole('button',{name:'Загрузить месячный документ Ozon'}));
    expect(await screen.findByRole('alert')).toHaveTextContent('Предыдущий сохранённый документ не заменён');
    expect(screen.getByText(/Документ: synthetic-report/)).toBeInTheDocument();
    expect(vi.mocked(fetch).mock.calls.filter(c=>c[1]?.method==='POST')).toHaveLength(1);
  });
  it('viewer can only read and month input excludes future and old unsupported periods',async()=>{
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue(response({loaded:false,rows:[]})));
    render(<OzonRealizationSource canWrite={false}/>);
    expect(screen.getByRole('button',{name:'Загрузить месячный документ Ozon'})).toBeDisabled();
    expect(screen.getByLabelText('Месяц документа Ozon')).toHaveAttribute('min','2023-08');
    await waitFor(()=>expect(fetch).toHaveBeenCalledTimes(1));
    expect(vi.mocked(fetch).mock.calls[0][1]?.method).toBe('GET');
  });
});
