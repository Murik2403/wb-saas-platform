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
  getDashboardSession: vi.fn(async () => ({user: {id:'u', name:'QA',role:'viewer'},tenant:{id:'t',name:'Учебный кабинет'},mode:'demo'})),
  getIncidentOperations: vi.fn(async () => ({cases:[],summary:{}})),
}));
import App from './App';
beforeEach(() => {
  dashboard = {...mock, sync:mock.dashboardSync, refreshing:false, refresh:vi.fn(async()=>undefined), connection:{mode:'live', label:'Данные обновлены',detail:'Учебная проверка'}, chartBarsWeek:mock.chartBars, sellerWarehouses:[],financeReport:{rows:[]},cashWindows:[],supplyNewOrders:[],acceptanceCoefficients:[],transitTariffs:[],supplyErrors:{},agentCandidates:[],agentHistory:[],productEconomics:[],productPrices:[],productPriceErrors:[],productCards:[],productCardErrors:[],customerQuality:null,dataQuality:null,procurementOrders:[]} as unknown as DashboardDataState;
  window.history.replaceState({},'', '/?view=today');
});
afterEach(cleanup);
describe('remaining cabinet audit acceptance', () => {
  it.each(['Не готово','not ok','broken','Неизвестно'])('keeps unconfirmed status %s out of completed checks', status => {
    expect(checkPriority(status)).not.toBe(3);
  });
  it('collapses the active incident creation form and exposes it on request', async () => {
    render(<App/>);
    const create = await screen.findByRole('button',{name:'Создать карточку'});
    expect(create).not.toBeVisible();
    const disclosure = create.closest('details');
    expect(disclosure).not.toHaveAttribute('open');
    fireEvent.click(disclosure!.querySelector('summary')!);
    expect(create).toBeVisible();
  });
  it('combines product-source failures into one useful warning without transport paths', () => {
    dashboard.productPriceErrors=['/api/v1/prices: HTTP 409'];
    dashboard.productCardErrors=['/api/v1/cards: HTTP 409'];
    window.history.replaceState({},'', '/?view=products');
    render(<App/>);
    expect(screen.getAllByText(/Часть данных WB недоступна/)).toHaveLength(1);
    expect(document.body.textContent).not.toMatch(/\/api\/v1|HTTP 409/);
  });
  it('keeps unknown checks actionable and visually distinct from confirmed checks', () => {
    dashboard.dataQuality={summary:{},reliability:[],fifo_guard:{},checks:[{Проверка:'Неопределённая проверка',Статус:'Неизвестно',Детали:'Нет подтверждения'},{Проверка:'Успешная проверка',Статус:'Готово',Детали:'Подтверждено'}]};
    window.history.replaceState({},'', '/?view=control');
    render(<App/>);
    expect(screen.getByText('Неопределённая проверка')).toBeVisible();
    expect(screen.getByText('Неизвестно')).not.toHaveClass('good');
    expect(screen.getByText('Успешная проверка')).not.toBeVisible();
  });
});
