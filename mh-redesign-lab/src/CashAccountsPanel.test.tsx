import {beforeEach,describe,expect,it,vi} from 'vitest';
import {fireEvent,render,screen,waitFor} from '@testing-library/react';
import {CashAccountsPanel} from './CashAccountsPanel';
import {cashAccountRequest,type AccountsReport,type StatementPreview} from './cash-accounts-source';
vi.mock('./cash-accounts-source',()=>({cashAccountRequest:vi.fn(),statementBody:(id:number,csv:string)=>({account_id:id,csv})}));
vi.mock('./cashflow-source',()=>({changeCashEntry:vi.fn()}));
const report:AccountsReport={accounts:[{id:2,name:'Банк',currency:'CNY',active:1,revision:1,legacy:0,opening_minor:0,actual_closing_minor:1250,forecast_closing_minor:2500,opening_confirmed:true,period_before_opening:false,overdue_minor:0,items:[],daily:[]}],external_inflow_rub_minor:0,external_outflow_rub_minor:0,transfer_valuation_delta_rub_minor:0};
const preview:StatementPreview={fingerprint:'a'.repeat(64),created:1,settled:0,skipped:0,rows:[{external_id:'bank-id',date:'2026-09-12',direction:'inflow',amount:'12.5',currency:'CNY',plan_id:null,action:'create',reference:'WB'}]};
describe('cash accounts',()=>{
  beforeEach(()=>{vi.resetAllMocks();vi.mocked(cashAccountRequest).mockImplementation(async(path)=>path.startsWith('?')?report:path.endsWith('/preview')?preview:{});});
  it('requires explicit checkbox after a readonly statement preview',async()=>{
    render(<CashAccountsPanel role="owner" start="2026-09-01" end="2026-09-20"/>);
    await screen.findAllByText('Банк · CNY');
    fireEvent.change(screen.getByLabelText('Выбранный счёт'),{target:{value:'2'}});
    fireEvent.change(screen.getByLabelText('CSV'),{target:{value:'csv content'}});
    document.querySelectorAll('details').forEach(element=>element.open=true);fireEvent.click(screen.getByRole('button',{name:'Проверить выписку'}));
    const confirm=await screen.findByRole('button',{name:'Подтвердить импорт'});
    expect(confirm).toBeDisabled();
    expect(vi.mocked(cashAccountRequest).mock.calls.some(([path])=>path.endsWith('/confirm'))).toBe(false);
    fireEvent.click(screen.getByRole('checkbox'));
    expect(confirm).not.toBeDisabled();
    fireEvent.change(screen.getByLabelText('CSV'),{target:{value:'edited csv'}});
    expect(screen.queryByRole('button',{name:'Подтвердить импорт'})).not.toBeInTheDocument();
  });
  it('retains identical import key after uncertain network result',async()=>{
    let tries=0;
    vi.mocked(cashAccountRequest).mockImplementation(async(path)=>{if(path.startsWith('?'))return report;if(path.endsWith('/preview'))return preview;if(path.endsWith('/confirm')&&tries++===0)throw new Error('Связь прервана');return {};});
    render(<CashAccountsPanel role="manager" start="2026-09-01" end="2026-09-20"/>);
    await screen.findAllByText('Банк · CNY');fireEvent.change(screen.getByLabelText('Выбранный счёт'),{target:{value:'2'}});
    document.querySelectorAll('details').forEach(element=>element.open=true);fireEvent.click(screen.getByRole('button',{name:'Проверить выписку'}));await screen.findByRole('checkbox');fireEvent.click(screen.getByRole('checkbox'));
    document.querySelectorAll('details').forEach(element=>element.open=true);fireEvent.click(screen.getByRole('button',{name:'Подтвердить импорт'}));await screen.findAllByText('Связь прервана');
    document.querySelectorAll('details').forEach(element=>element.open=true);fireEvent.click(screen.getByRole('button',{name:'Подтвердить импорт'}));
    await waitFor(()=>expect(vi.mocked(cashAccountRequest).mock.calls.filter(([path])=>path.endsWith('/confirm'))).toHaveLength(2));
    const calls=vi.mocked(cashAccountRequest).mock.calls.filter(([path])=>path.endsWith('/confirm'));
    expect(calls[0][2]).toEqual(calls[1][2]);
  });
  it('viewer can inspect preview but cannot create accounts or confirm imports',async()=>{
    render(<CashAccountsPanel role="viewer" start="2026-09-01" end="2026-09-20"/>);await screen.findAllByText('Банк · CNY');
    expect(screen.queryByRole('button',{name:'Создать счёт'})).not.toBeInTheDocument();
    fireEvent.change(screen.getByLabelText('Выбранный счёт'),{target:{value:'2'}});document.querySelectorAll('details').forEach(element=>element.open=true);fireEvent.click(screen.getByRole('button',{name:'Проверить выписку'}));
    await screen.findAllByText('bank-id');expect(screen.queryByRole('checkbox')).not.toBeInTheDocument();
    expect(screen.queryByRole('button',{name:'Подтвердить импорт'})).not.toBeInTheDocument();
    expect(screen.getByText('Факт: 12,50 CNY')).toBeInTheDocument();
  });
});
