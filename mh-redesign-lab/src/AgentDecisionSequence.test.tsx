import '@testing-library/jest-dom/vitest';
import {cleanup,fireEvent,render,screen,within,waitFor} from '@testing-library/react';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
import * as mock from './mock-dashboard';
import type {DashboardDataState} from './data-source';
vi.mock('./ShopContextBoundary',()=>({ShopContextBoundary:({children}:{children:unknown})=>children}));
let dashboard:DashboardDataState;
const decide=vi.hoisted(()=>vi.fn(async()=>undefined));
vi.mock('./data-source',async()=>({
 ...await vi.importActual<typeof import('./data-source')>('./data-source'),
 useDashboardData:()=>dashboard,
 decideAgentCandidate:decide,
 getActionPlanItems:vi.fn(async()=>[]),
 getDashboardSession:vi.fn(async()=>({user:{id:'u',name:'QA',role:'owner'},tenant:{id:'t',name:'Учебный кабинет'},mode:'live'})),
 getIncidentOperations:vi.fn(async()=>({cases:[],summary:{}})),
}));
import App from './App';
beforeEach(()=>{
 decide.mockClear();
 dashboard={...mock,sync:mock.dashboardSync,refreshing:false,refresh:vi.fn(async()=>undefined),connection:{mode:'live',label:'Данные обновлены',detail:'Учебная проверка'},chartBarsWeek:mock.chartBars,sellerWarehouses:[],financeReport:{rows:[]},cashWindows:[],supplyNewOrders:[],acceptanceCoefficients:[],transitTariffs:[],supplyErrors:{},agentCandidates:[1,2].map(id=>({id,agent:'price',marketplace:'wb',targetId:String(id),targetName:'Учебный товар '+id,action:'price_change',currentValue:'100 ₽',proposedValue:'110 ₽',reason:'Учебное основание'})),agentHistory:[],productEconomics:[],productPrices:[],productPriceErrors:[],productCards:[],productCardErrors:[],customerQuality:null,dataQuality:null,procurementOrders:[]} as unknown as DashboardDataState;
 window.history.replaceState({},'','/?view=assistant');
});
afterEach(cleanup);
it('allows a second recommendation after the first confirmation succeeds',async()=>{
 render(<App/>);
 fireEvent.click(screen.getAllByRole('button',{name:'Применить'})[0]);
 let dialog=screen.getByRole('dialog',{name:'Применить рекомендацию?'});
 fireEvent.click(within(dialog).getByRole('button',{name:'Применить'}));
 await waitFor(()=>expect(screen.queryByRole('dialog',{name:'Применить рекомендацию?'})).not.toBeInTheDocument());
 expect(decide).toHaveBeenCalledWith(1,'apply');
 fireEvent.click(screen.getAllByRole('button',{name:'Применить'})[1]);
 dialog=screen.getByRole('dialog',{name:'Применить рекомендацию?'});
 expect(within(dialog).getByRole('button',{name:'Применить'})).toBeEnabled();
 fireEvent.click(within(dialog).getByRole('button',{name:'Применить'}));
 await waitFor(()=>expect(decide).toHaveBeenCalledWith(2,'apply'));
});
