// Run the real template script without a network or a browser dependency.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const template = fs.readFileSync(path.join(__dirname, '../templates/marketing_calculator.html'), 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];
const names = ['calcPrice', 'calcCost', 'calcCommission', 'calcLogistics', 'calcAds'];
const nodes = {};
names.concat(['calcProfit', 'calcMargin', 'calcError']).forEach(id => {
  nodes[id] = { value: '', textContent: '', attrs: {}, hidden: false, listeners: {},
    setAttribute(key,value) { this.attrs[key] = value; },
    addEventListener(event, callback) { this.listeners[event] = callback; } };
});
function setValues(values) { names.forEach((name,i) => { nodes[name].value = String(values[i]); }); }
setValues([1200, 420, 17, 90, 60]);
vm.runInNewContext(script, { document: {getElementById: id => nodes[id]} });
function run(values) { setValues(values); nodes.calcPrice.listeners.input(); }
assert.equal(nodes.calcProfit.textContent, '426 ₽');
assert.equal(nodes.calcMargin.textContent, '35,5%');
run([100, 110, 0, 0, 0]);
assert.equal(nodes.calcProfit.textContent, '-10 ₽');
assert.equal(nodes.calcProfit.className, 'calc-big critical');
run([0, 10, 0, 0, 0]);
assert.match(nodes.calcMargin.textContent, /нулевой цене/);
for (const invalid of [[-1, 10, 17, 0, 0], [1200, 10, 101, 0, 0], ['', 10, 17, 0, 0], [1200, 'bad', 17, 0, 0]]) {
  run(invalid);
  assert.equal(nodes.calcProfit.textContent, '—');
  assert.equal(nodes.calcError.hidden, false);
}
run([100, 0, 100, 0, 0]);
assert.equal(nodes.calcProfit.textContent, '0 ₽');
assert.equal(nodes.calcError.hidden, true);
run([100.5, 20.25, 10, 0, 0]);
assert.equal(nodes.calcProfit.textContent, '70,2 ₽');
console.log('Calculator: default, loss, zero price, invalid/empty inputs, boundaries and decimals passed.');
