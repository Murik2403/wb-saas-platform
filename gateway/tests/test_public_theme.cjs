const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../static/theme.js'), 'utf8');

function page(saved, blocked = false) {
  const attributes = {};
  const listeners = {};
  const button = { setAttribute(k, v) { this[k] = v; }, addEventListener(k, fn) { this[k] = fn; } };
  const meta = { setAttribute(k, v) { this[k] = v; } };
  let ready = false;
  const storage = { value: saved, getItem() { if (blocked) throw Error('disabled'); return this.value; }, setItem(k, v) { if (blocked) throw Error('disabled'); this.value = v; } };
  const document = {
    documentElement: { setAttribute(k, v) { attributes[k] = v; }, getAttribute(k) { return attributes[k]; } },
    querySelector() { return meta; }, querySelectorAll() { return ready ? [button] : []; },
    addEventListener(k, fn) { listeners[k] = fn; },
  };
  vm.runInNewContext(source, { document, window: { localStorage: storage, addEventListener(k, fn) { listeners[k] = fn; } } });
  return { attributes, button, meta, storage, ready() { ready = true; listeners.DOMContentLoaded(); }, external(value) { listeners.storage({ key: 'marketshelper-theme', newValue: value }); } };
}

test('saved light palette is restored before DOM ready; toggle persists both directions', () => {
  const p = page('light');
  assert.equal(p.attributes['data-theme'], 'light');
  assert.equal(p.meta.content, '#f6f7fb');
  p.ready();
  assert.equal(p.button['aria-pressed'], 'true');
  assert.equal(p.button['aria-label'], 'Включить тёмную тему');
  p.button.click();
  assert.equal(p.storage.value, 'dark');
  p.button.click();
  assert.equal(p.storage.value, 'light');
});
test('disabled storage keeps an operational toggle with a dark default', () => {
  const p = page(null, true); p.ready();
  assert.equal(p.attributes['data-theme'], 'dark');
  p.button.click();
  assert.equal(p.attributes['data-theme'], 'light');
});
test('other tabs update the palette and unknown values fall back to dark', () => {
  const p = page('unexpected'); p.ready();
  assert.equal(p.attributes['data-theme'], 'dark');
  p.external('light'); assert.equal(p.button['aria-pressed'], 'true');
  p.external(null); assert.equal(p.attributes['data-theme'], 'dark');
});
