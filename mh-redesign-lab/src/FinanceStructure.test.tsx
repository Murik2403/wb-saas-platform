import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { FinanceReportState } from './contracts';
vi.mock('./ManagementPnlSummary', () => ({ ManagementPnlSummary: () => <section aria-label="Управленческий результат">Сводка P&amp;L</section> }));
vi.mock('./ShopWorkspaces', () => ({ ShopWorkspaces: () => null }));
vi.mock('./OzonAccounting', () => ({ OzonAccounting: () => null }));
vi.mock('./PriceScenarios', () => ({ PriceScenarios: () => null }));
import { FinanceView } from './App';
beforeEach(() => window.localStorage.clear());
afterEach(cleanup);
const tab = (name: string) => fireEvent.click(screen.getByRole('button', { name }));
const follows = (order: HTMLElement[]) => order.slice(1).forEach((node, i) => expect(order[i].compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy());
const report: FinanceReportState = {
  rows: [{ label: 'Логистика', amount: -15 }], complete: false, lastDate: '29.09.2026', lagDays: 1,
  financialProfitRub: 50, balance: 100, forWithdraw: 80,
  fifoPeriodBasis: 'sale_date', fifoCosts: [],
  cogsEvidence: { exactFifoRub: 40, currentEstimateRub: 10, exactUnits: 4, estimatedUnits: 1, insufficientUnits: 0, totalUnits: 5, exactUnitCoveragePercent: 80, quality: 'mixed', complete: true },
  fifoReconciliation: { saleReturnRows: 5, exactRows: 4, saleReturnUnits: 5, exactUnits: 4, exactUnitCoveragePercent: 80, exactFifoCogs: 40, statusCounts: { exact: 4, unmatched: 1 } },
};
function mount(financeReport = report) {
  return render(<FinanceView cashflow={<section aria-label="Движение денег по счетам">Учёт денежных операций</section>} financeReport={financeReport} financeMetrics={[]} financeLedger={[]} expenseSplit={[]} cashWindows={[]} onAction={vi.fn()} period={{ id: '30d' }} setPeriod={vi.fn()} />);
}
describe('finance reading order and cost disclosure', () => {
  it('puts money and expenses before optional cost verification, with expenses beside their share', () => {
    const { container } = mount();
    follows([screen.getByText('Баланс WB'), screen.getByRole('region', { name: 'Управленческий результат' }), screen.getByRole('heading', { name: 'Денежное движение' })]);
    tab('Счета и кассовые окна');
    follows([screen.getByRole('region', { name: 'Движение денег по счетам' }), screen.getByRole('heading', { name: 'Кассовые окна' })]);
    tab('Расходы и себестоимость');
    follows([screen.getByRole('heading', { name: 'Статьи расходов' }), screen.getByText('Проверка себестоимости')]);
    const grid = container.querySelector('.finance-expenses-grid');
    expect(grid).toContainElement(screen.getByRole('heading', { name: 'Статьи расходов' }));
    expect(grid).toContainElement(screen.getByRole('heading', { name: 'Доля расходов' }));
    expect(screen.getByText('WB обновляет данные')).toBeVisible();
  });
  it('hides cost breakdown by default, keeps preliminary warning visible and discloses original amounts', () => {
    const { container } = mount();
    tab('Расходы и себестоимость');
    const disclosure = container.querySelector('details.finance-cost-review')!;
    expect(disclosure).not.toHaveAttribute('open');
    expect(screen.getByText(/Сумма прибыли предварительная/)).toBeVisible();
    expect(screen.getByText('Точный исторический FIFO')).not.toBeVisible();
    expect(screen.getByRole('button', { name: 'Показать строки' })).not.toBeVisible();
    fireEvent.click(disclosure.querySelector('summary')!);
    expect(disclosure).toHaveAttribute('open');
    expect(screen.getByText('Точный исторический FIFO')).toBeVisible();
    expect(screen.getByText('4 / 5 ед.')).toBeVisible();
    expect(screen.getByRole('button', { name: 'Показать строки' })).toBeVisible();
    expect(screen.getByText('Требуют проверки: нет связанной продажи — 1')).toBeVisible();
    expect(disclosure.textContent).not.toMatch(/rrd_id|nm_id|srid/i);
  });
  it('keeps missing cost warning outside the collapsed detail and never relabels it as confirmed', () => {
    mount({ ...report, cogsEvidence: { ...report.cogsEvidence!, quality: 'insufficient', complete: false, insufficientUnits: 1 } });
    tab('Расходы и себестоимость');
    expect(screen.getByText(/Для этих единиц нельзя считать себестоимость равной нулю/)).toBeVisible();
    expect(screen.getByText('В прибыли есть строки без себестоимости')).not.toBeVisible();
  });
});
describe('finance tabs', () => {
  const only = (visible: string[]) => {
    const all: Array<[string, string]> = [['Управленческий результат', 'region'], ['Денежное движение', 'heading'], ['Движение денег по счетам', 'region'], ['Налоговый подраздел Финансов', 'region'], ['Статьи расходов', 'heading']];
    return all.filter(([n, role]) => screen.queryByRole(role as 'region', { name: n })).map(([n]) => n).sort().toString() === visible.slice().sort().toString();
  };
  it('each tab shows only its own blocks', () => {
    mount();
    expect(only(['Управленческий результат', 'Денежное движение'])).toBe(true);
    tab('Счета и кассовые окна'); expect(only(['Движение денег по счетам'])).toBe(true);
    tab('Налоги'); expect(only(['Налоговый подраздел Финансов'])).toBe(true);
    tab('Расходы и себестоимость'); expect(only(['Статьи расходов'])).toBe(true);
  });
  it('remembers the tab and lets a deep-link target override it', () => {
    mount(); tab('Расходы и себестоимость'); cleanup();
    mount();
    expect(screen.getByRole('button', { name: 'Расходы и себестоимость' })).toHaveAttribute('aria-pressed', 'true');
    cleanup();
    render(<FinanceView target="tax-reconciliation" cashflow={null} financeReport={report} financeMetrics={[]} financeLedger={[]} expenseSplit={[]} cashWindows={[]} onAction={vi.fn()} period={{ id: '30d' }} setPeriod={vi.fn()} />);
    expect(screen.getByRole('button', { name: 'Налоги' })).toHaveAttribute('aria-pressed', 'true');
    expect(within(screen.getByRole('region', { name: 'Налоги' })).getByRole('region', { name: 'Налоговый подраздел Финансов' })).toBeVisible();
  });
  it('opens Налоги for the ?view=finance#tax-reconciliation link', () => {
    window.history.replaceState({}, '', '/?view=finance#tax-reconciliation');
    mount();
    expect(screen.getByRole('button', { name: 'Налоги' })).toHaveAttribute('aria-pressed', 'true');
    window.history.replaceState({}, '', '/');
  });
});
