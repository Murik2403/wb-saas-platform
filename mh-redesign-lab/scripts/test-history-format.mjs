import assert from 'node:assert/strict';
import { historyDate, syncSummary } from '../src/history-format.ts';

assert.equal(historyDate('2026-09-09T20:54:21.324318'), '09.09.2026 · 20:54');
assert.equal(historyDate(null), '—');
const partial = syncSummary('partial', 'orders: 138, sales: 80, ads_error: WB API 500: {"origin":"camp-api-public-cache","status":500}');
assert.match(partial.counts, /заказы: 138/);
assert.match(partial.counts, /продажи: 80/);
assert.ok(!partial.counts.includes('500'));
assert.match(partial.notice, /Реклама/);
assert.ok(!partial.notice.includes('origin'));
assert.equal(syncSummary('success', 'orders: 0').notice, '');
assert.equal(syncSummary('error', 'unrecognized failure').title, 'Не удалось завершить обновление');
console.log('History formatting checks passed');
