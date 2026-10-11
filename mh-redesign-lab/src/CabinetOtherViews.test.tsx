import {lazyLoaded} from './test-lazy';
import { renderAuthorizedWb } from "./test-fixtures/marketplace-app-fixture";
import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import * as mock from './mock-dashboard';
import type { DashboardDataState } from './data-source';
import { checkPriority } from './cabinet-copy';
vi.mock('./ShopContextBoundary', () => ({ ShopContextBoundary: ({children}: {children: unknown}) => children }));
let dashboard: DashboardDataState;
vi.mock('./data-source', async () => ({
  ...await vi.importActual<typeof import('./data-source')>('./data-source'),
  useDashboardData: () => dashboard,
  getActionPlanItems: vi.fn(async () => []),
  getProductGroups: vi.fn(async () => []),
  getTenantSettings: vi.fn(async()=>({sync_interval_minutes:30,initial_history_days:90,agent_interval_minutes:60,wb_connected:false,ozon_connected:false})),
  getTenantBackups: vi.fn(async()=>({backups:[]})),
  getTenantSyncHistory: vi.fn(async()=>({runs:[]})),
  getProductCostSettings: vi.fn(async()=>({items:[{nm_id:123,product_name:'Manager product',supplier_article:'M',cost_per_wb_unit:100,note:''}]})),
  getProductionNorms: vi.fn(async()=>({items:[{nm_id:123,product_name:'Manager product',supplier_article:'M',material_per_unit:2,material_unit:'м',min_batch:10,pack_size:4,enabled:1}]})),
  getMaterialInventory: vi.fn(async()=>({items:[],fifo:[],opening_rate_rub_m:0})),
  getWBTokenStatus: vi.fn(async()=>({scopes:[],missing_scopes:[],connected:false})),
  getManualExpenses: vi.fn(async()=>({items:[]})),
  getActivationSummary: vi.fn(async()=>({is_demo:false,wb_connected:false,sync:{state:'error',complete:false},selected_product:{nm_id:123,cost_configured:true,has_financial_rows:false},ready_for_report:false,next_step:'connect',first_value_verified:false})),
  updateProductCostSettings: vi.fn(async()=>({saved:1})),
  updateProductionNorms: vi.fn(async()=>({saved:1})),
  getDashboardSession: vi.fn(async () => ({user: {id:'u', name:'QA',role:'viewer'},tenant:{id:'t',name:'Учебный кабинет'},mode:'demo'})),
  getIncidentOperations: vi.fn(async () => ({cases:[],summary:{}})),
}));
import App from './App';
import {getDashboardSession,getTenantBackups,updateProductCostSettings,updateProductionNorms} from './data-source';
beforeEach(() => {
  dashboard = {...mock, sync:mock.dashboardSync, refreshing:false, refresh:vi.fn(async()=>undefined), connection:{mode:'live', label:'Данные обновлены',detail:'Учебная проверка'}, chartBarsWeek:mock.chartBars, sellerWarehouses:[],financeReport:{rows:[]},cashWindows:[],supplyNewOrders:[],acceptanceCoefficients:[],transitTariffs:[],supplyErrors:{},agentCandidates:[],agentHistory:[],productEconomics:[],productPrices:[],productPriceErrors:[],productCards:[],productCardErrors:[],customerQuality:null,dataQuality:null,procurementOrders:[]} as unknown as DashboardDataState;
  window.history.replaceState({},'', '/?view=today');
});
afterEach(cleanup);
describe('remaining cabinet audit acceptance', () => {
  it('manager can save cost and production norm while API onboarding stays owner-only', async()=>{
    vi.mocked(getDashboardSession).mockResolvedValueOnce({user:{id:'u',name:'Manager',role:'manager'},tenant:{id:'t',name:'Cabinet'},mode:'live'});
    window.history.replaceState({},'', '/?view=settings');
    await renderAuthorizedWb(<App/>);
    for(const button of await screen.findAllByRole('button',{name:'Проверить и сохранить'})) expect(button).toBeDisabled();
    await lazyLoaded();
    expect(getTenantBackups).not.toHaveBeenCalled();
    fireEvent.click(screen.getAllByRole('button',{name:'Производство'}).find(button=>button.hasAttribute('aria-pressed'))!);
    await lazyLoaded();
    fireEvent.click(await screen.findByRole('button',{name:'Изменить себестоимость: Manager product'}));
    fireEvent.change(screen.getByLabelText('Себестоимость одной единицы, ₽'),{target:{value:'125'}});
    fireEvent.click(screen.getByRole('button',{name:'Сохранить'}));
    await screen.findByText(/Себестоимость.*сохранена/);
    expect(updateProductCostSettings).toHaveBeenCalledWith([expect.objectContaining({nm_id:123,cost_per_wb_unit:125})],'');
    fireEvent.click(screen.getByRole('button',{name:'Изменить норму'}));
    fireEvent.change(screen.getByLabelText('Расход на комплект, м'),{target:{value:'3'}});
    fireEvent.click(screen.getByRole('button',{name:'Сохранить норму'}));
    await screen.findByText(/Норма.*сохранена/);
    expect(updateProductionNorms).toHaveBeenCalledWith([expect.objectContaining({nm_id:123,material_per_unit:3})],'');
  });
  it.each(['Не готово','not ok','broken','Неизвестно'])('keeps unconfirmed status %s out of completed checks', status => {
    expect(checkPriority(status)).not.toBe(3);
  });
  it('collapses the active incident creation form and exposes it on request', async () => {
    await renderAuthorizedWb(<App/>);
    fireEvent.click(await screen.findByRole('button',{name:'Инциденты'}));
    const create = await screen.findByRole('button',{name:'Создать карточку'});
    expect(create).not.toBeVisible();
    const disclosure = create.closest('details');
    expect(disclosure).not.toHaveAttribute('open');
    fireEvent.click(disclosure!.querySelector('summary')!);
    expect(create).toBeVisible();
  });
  it('combines product-source failures into one useful warning without transport paths', async () => {
    dashboard.productPriceErrors=['/api/v1/prices: HTTP 409'];
    dashboard.productCardErrors=['/api/v1/cards: HTTP 409'];
    window.history.replaceState({},'', '/?view=products');
    await renderAuthorizedWb(<App/>);
    await lazyLoaded();
    expect(screen.getAllByText(/Часть данных WB недоступна/)).toHaveLength(1);
    expect(document.body.textContent).not.toMatch(/\/api\/v1|HTTP 409/);
  });
  it('keeps unknown checks actionable and visually distinct from confirmed checks', async () => {
    dashboard.dataQuality={summary:{},reliability:[],fifo_guard:{},checks:[{Проверка:'Неопределённая проверка',Статус:'Неизвестно',Детали:'Нет подтверждения'},{Проверка:'Успешная проверка',Статус:'Готово',Детали:'Подтверждено'}]};
    window.history.replaceState({},'', '/?view=control');
    await renderAuthorizedWb(<App/>);
    await lazyLoaded();
    expect(screen.getByText('Неопределённая проверка')).toBeVisible();
    expect(screen.getByText('Неизвестно')).not.toHaveClass('good');
    expect(screen.queryByText('Успешная проверка')).toBeNull();
    fireEvent.click(screen.getByRole('button',{name:'В порядке · 1'}));
    expect(screen.getByText('Успешная проверка')).toBeVisible();
  });
});
