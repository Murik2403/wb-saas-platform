// Run the real template script without a network or a browser dependency.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const template = fs.readFileSync(path.join(__dirname, '../templates/marketing_calculator.html'), 'utf8');
const script = template.match(/<script>([\s\S]*?)<\/script>/)[1];
const names = ['calcPrice', 'calcCost', 'calcCommission', 'calcLogistics', 'calcAds'];
const defaults = [1200, 420, 17, 90, 60];

function load(search) {
  const nodes = {};
  names.concat(['calcProfit', 'calcMargin', 'calcError', 'calcDownload']).forEach(id => {
    nodes[id] = { value: '', textContent: '', attrs: {}, hidden: false, listeners: {},
      setAttribute(key, value) { this.attrs[key] = value; },
      addEventListener(event, callback) { this.listeners[event] = callback; } };
  });
  names.forEach((name, index) => { nodes[name].value = String(defaults[index]); });
  const saved = { urls: [], files: [], csv: null };
  const sandbox = {
    document: {
      getElementById: id => nodes[id],
      createElement: () => ({ click() { saved.files.push(this.download); } }),
      body: { appendChild() {}, removeChild() {} },
    },
    URLSearchParams,
    location: { search: search || '', pathname: '/calculator' },
    history: { replaceState(state, title, url) { saved.urls.push(url); } },
    Blob: function Blob(parts) { this.parts = parts; },
    URL: { createObjectURL: blob => { saved.csv = blob.parts.join(''); return 'blob:test'; }, revokeObjectURL() {} },
  };
  vm.runInNewContext(script, sandbox);
  const run = values => { names.forEach((name, i) => { nodes[name].value = String(values[i]); }); nodes.calcPrice.listeners.input(); };
  return { nodes, saved, run };
}

// --- formula, unchanged behaviour -------------------------------------------
const { nodes, run } = load();
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
  assert.equal(nodes.calcDownload.disabled, true, 'a broken calculation must not be downloadable');
}
run([100, 0, 100, 0, 0]);
assert.equal(nodes.calcProfit.textContent, '0 ₽');
assert.equal(nodes.calcError.hidden, true);
run([100.5, 20.25, 10, 0, 0]);
assert.equal(nodes.calcProfit.textContent, '70,2 ₽');

// --- CSV export --------------------------------------------------------------
{
  const page = load();
  assert.equal(page.nodes.calcDownload.disabled, false);
  page.nodes.calcDownload.listeners.click();
  assert.deepEqual(page.saved.files, ['marketshelper-raschet.csv']);
  assert.ok(page.saved.csv.startsWith('﻿'), 'Excel needs the BOM to read UTF-8');
  assert.deepEqual(page.saved.csv.split('\r\n'), [
    '﻿Показатель;Сумма ₽',
    'Цена продажи;1200',
    'Себестоимость;-420',
    'Комиссия WB 17%;-204',
    'Логистика и упаковка;-90',
    'Реклама на единицу;-60',
    'Предварительная маржа;426',
    'Доля от цены продажи %;35,5',
    '',
  ]);
  page.run([0, 10, 0, 0, 0]);
  page.nodes.calcDownload.listeners.click();
  assert.match(page.saved.csv, /Доля от цены продажи %;не определена/, 'zero price must not divide');
  page.run([1200, 'bad', 17, 0, 0]);
  page.saved.csv = null;
  page.nodes.calcDownload.listeners.click();
  assert.equal(page.saved.csv, null, 'an invalid form exports nothing');
}

// --- permalink ---------------------------------------------------------------
{
  const page = load();
  assert.deepEqual(page.saved.urls, [], 'a plain visit must not rewrite the address');
  page.run([1000, 100, 10, 20, 30]);
  assert.deepEqual(page.saved.urls, ['/calculator?price=1000&cost=100&commission=10&logistics=20&ads=30']);
}
{
  // Values from the query string are clamped; junk falls back to the default.
  const page = load('?price=1000&cost=100&commission=500&logistics=abc&ads=-5');
  assert.equal(page.nodes.calcPrice.value, '1000');
  assert.equal(page.nodes.calcCost.value, '100');
  assert.equal(page.nodes.calcCommission.value, '100');
  assert.equal(page.nodes.calcLogistics.value, '90');
  assert.equal(page.nodes.calcAds.value, '60');
}
{
  const page = load('?price=%3Cimg%20src%3Dx%20onerror%3Dalert(1)%3E&cost=');
  assert.equal(page.nodes.calcPrice.value, '1200', 'markup in the URL never reaches the form');
  assert.equal(page.nodes.calcCost.value, '420');
  assert.equal(page.nodes.calcProfit.textContent, '426 ₽');
}

console.log('Calculator: formula, CSV export, permalink restore/clamp and invalid input handling passed.');
