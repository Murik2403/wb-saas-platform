import { renderAuthorizedWb } from "./test-fixtures/marketplace-app-fixture";
import '@testing-library/jest-dom/vitest';
import {cleanup,fireEvent,render,screen,within} from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import * as mock from './mock-dashboard';
import type {DashboardDataState} from './data-source';
vi.mock('./ShopContextBoundary',()=>({ShopContextBoundary:({children}:{children:unknown})=>children}));
let dashboard:DashboardDataState;
vi.mock('./data-source',async()=>({
 ...await vi.importActual<typeof import('./data-source')>('./data-source'),
 useDashboardData:()=>dashboard,
 getActionPlanItems:vi.fn(async()=>[]),
 getDashboardSession:vi.fn(async()=>({user:{id:'u',name:'QA',role:'viewer'},tenant:{id:'t',name:'Учебный кабинет'},mode:'demo'})),
 getIncidentOperations:vi.fn(async()=>({cases:[],summary:{}})),
}));
import App from './App';
beforeEach(()=>{
 dashboard={...mock,sync:mock.dashboardSync,refreshing:false,refresh:vi.fn(async()=>undefined),connection:{mode:'live',label:'Данные обновлены',detail:'Учебная проверка'},chartBarsWeek:mock.chartBars,sellerWarehouses:[],financeReport:{rows:[]},cashWindows:[],supplyNewOrders:[],acceptanceCoefficients:[],transitTariffs:[],supplyErrors:{},agentCandidates:[],agentHistory:[],productEconomics:[],productPrices:[],productPriceErrors:[],productCards:[],productCardErrors:[],customerQuality:null,dataQuality:null,procurementOrders:[]} as unknown as DashboardDataState;
 window.history.replaceState({},'','/?view=today');
});
afterEach(cleanup);
it('keeps keyboard navigation inside the mobile dialog and restores the opener on Escape',async()=>{
 const user=userEvent.setup();await renderAuthorizedWb(<App/>);
 const opener=screen.getByRole('button',{name:'Открыть меню'});opener.focus();fireEvent.click(opener);
 const dialog=screen.getByRole('dialog',{name:'Все разделы'});
 const close=within(dialog).getByRole('button',{name:'Закрыть меню'});
 expect(dialog).toHaveAttribute('aria-modal','true');expect(close).toHaveFocus();
 const last=within(dialog).getAllByRole('button').at(-1)!;
 await user.tab({shift:true});expect(last).toHaveFocus();
 await user.tab();expect(close).toHaveFocus();
 await user.keyboard('{Escape}');
 expect(screen.queryByRole('dialog',{name:'Все разделы'})).not.toBeInTheDocument();expect(opener).toHaveFocus();
});
it('closes with its explicit button or a selected section without changing page navigation',async () => {
 await renderAuthorizedWb(<App/>);const opener=screen.getByRole('button',{name:'Открыть меню'});opener.focus();fireEvent.click(opener);
 fireEvent.click(within(screen.getByRole('dialog',{name:'Все разделы'})).getByRole('button',{name:'Закрыть меню'}));
 expect(opener).toHaveFocus();expect(opener).toHaveAttribute('aria-expanded','false');
 fireEvent.click(opener);
 fireEvent.click(within(screen.getByRole('dialog',{name:'Все разделы'})).getByRole('button',{name:/Товары/}));
 expect(screen.queryByRole('dialog',{name:'Все разделы'})).not.toBeInTheDocument();
 expect(window.location.search).toContain('view=products');expect(screen.getByRole('heading',{level:1})).toHaveTextContent(/Товары|Экономика/);
});
