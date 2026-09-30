import '@testing-library/jest-dom/vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { FinanceReportState } from './contracts';
vi.mock('./ManagementPnlSummary', () => ({ ManagementPnlSummary: () => <section aria-label="Управленческий результат">Сводка P&amp;L</section> }));
vi.mock('./ShopWorkspaces', () => ({ ShopWorkspaces: () => null }));
vi.mock('./OzonAccounting', () => ({ OzonAccounting: () => null }));
vi.mock('./PriceScenarios', () => ({ PriceScenarios: () => null }));
import { FinanceView } from './App';
afterEach(cleanup);
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
    const order = [screen.getByText('Баланс WB'), screen.getByRole('region', { name: 'Управленческий результат' }), screen.getByRole('region', { name: 'Движение денег по счетам' }), screen.getByRole('heading', { name: 'Денежное движение' }), screen.getByRole('heading', { name: 'Статьи расходов' }), screen.getByRole('heading', { name: 'Кассовые окна' }), screen.getByText('Проверка себестоимости')];
    order.slice(1).forEach((node, i) => expect(order[i].compareDocumentPosition(node) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy());
    const grid = container.querySelector('.finance-expenses-grid');
    expect(grid).toContainElement(screen.getByRole('heading', { name: 'Статьи расходов' }));
    expect(grid).toContainElement(screen.getByRole('heading', { name: 'Доля расходов' }));
    expect(screen.getByText('WB обновляет данные')).toBeVisible();
  });
  it('hides cost breakdown by default, keeps preliminary warning visible and discloses original amounts', () => {
    const { container } = mount();
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
    expect(screen.getByText(/Для этих единиц нельзя считать себестоимость равной нулю/)).toBeVisible();
    expect(screen.getByText('В прибыли есть строки без себестоимости')).not.toBeVisible();
  });
});
