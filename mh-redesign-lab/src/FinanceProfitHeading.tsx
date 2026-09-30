import type { FinanceReportState } from './contracts';

export function FinanceProfitHeading({report}:{report:FinanceReportState}) {
  const amount=report.financialProfitRub;
  if (typeof amount!=='number' || !Number.isFinite(amount)) return <h2>Финансовый результат WB не рассчитан</h2>;
  const exact=report.cogsEvidence?.quality==='exact'||report.cogsEvidence?.quality==='no_sales';
  return <h2>{Math.round(amount).toLocaleString('ru-RU')} ₽ {exact?'расчётного':'предварительного'} результата WB</h2>;
}
