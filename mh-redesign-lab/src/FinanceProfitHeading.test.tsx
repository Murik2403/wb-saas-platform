import {render,screen,cleanup} from '@testing-library/react';
import {afterEach,describe,it,expect} from 'vitest';
import '@testing-library/jest-dom/vitest';
import {FinanceProfitHeading} from './FinanceProfitHeading';
import {adaptFinanceReport} from './data-source';
afterEach(cleanup);
describe('realized finance hero',()=>{
 it.each([[10,80],[30,60]])('uses same realized report profit with cost%s independently of absent operational KPIs',(_cost,profit)=>{
   const report=adaptFinanceReport({financial:{report_rows:1,profit,cogs_quality:'estimated',current_cost_estimate_rub:_cost}}, {rows:[]});
   render(<FinanceProfitHeading report={report}/>);expect(screen.getByRole('heading')).toHaveTextContent(`${profit} ₽ предварительного результата WB`);
 });
 it.each([{}, {report_rows:0,profit:0},{report_rows:1,profit:null},{report_rows:1,profit:Infinity},{report_rows:1,profit:'80'}])('missing/unverified report amount remains unknown: %j',financial=>{
   render(<FinanceProfitHeading report={adaptFinanceReport({financial},{rows:[]})}/>);expect(screen.getByRole('heading')).toHaveTextContent('не рассчитан');expect(screen.getByRole('heading')).not.toHaveTextContent('0 ₽');
 });
 it('preserves actual zero in a present report and exact cost label',()=>{
   render(<FinanceProfitHeading report={adaptFinanceReport({financial:{report_rows:1,profit:0,cogs_quality:'exact'}},{rows:[]})}/>);expect(screen.getByRole('heading')).toHaveTextContent('0 ₽ расчётного результата WB');
 });
});
