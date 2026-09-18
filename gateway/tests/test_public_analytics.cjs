// Execute the actual browser script in an isolated VM. DOM insertion and
// fetch are local fakes: these tests cannot load a tracker or contact a host.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const test = require('node:test');

const script = fs.readFileSync(path.join(__dirname, '../static/public-analytics.js'), 'utf8');
const storageKey = 'marketshelper_cookie_notice_v2';
const plain = value => JSON.parse(JSON.stringify(value));

function deferred() {
  let resolve;
  let reject;
  const promise = new Promise((yes, no) => { resolve = yes; reject = no; });
  return { promise, resolve, reject };
}

function boot({ choice = '', available = true, counter = '12345678', href = 'https://marketshelper.test/', stored = {}, respond } = {}) {
  const scripts = [];
  const requests = [];
  const storage = new Map(Object.entries(stored));
  let reloads = 0;
  const location = new URL(href);
  location.reload = () => { reloads += 1; };
  const nodes = {};
  for (const id of ['privacy-settings', 'cookie-notice', 'cookie-status', 'cookie-accept', 'cookie-decline', 'cookie-settings-open']) {
    nodes[id] = {
      dataset: {}, hidden: false, textContent: '', focused: false, listeners: {},
      addEventListener(event, callback) { this.listeners[event] = callback; },
      focus() { this.focused = true; },
    };
  }
  nodes['privacy-settings'].dataset = { choice, available: String(available), counter };
  const window = { location };
  const fetch = (url, options) => {
    requests.push({ url, options: plain(options) });
    return respond ? respond(url, options) : Promise.resolve({ ok: true });
  };
  const document = {
    getElementById: id => nodes[id],
    createElement: tagName => ({ tagName }),
    head: { appendChild(element) { scripts.push(element); } },
  };
  vm.runInNewContext(script, {
    window, document, fetch, URL, URLSearchParams,
    localStorage: {
      getItem: key => storage.get(key) ?? null,
      setItem: (key, value) => storage.set(key, String(value)),
    },
  }, { filename: 'public-analytics.js' });
  return {
    scripts, requests, storage, nodes,
    events: () => plain((window.ym?.a || []).map(args => Array.from(args))),
    click: id => nodes[id].listeners.click(),
    reloads: () => reloads,
  };
}

test('before current server consent, old local choices cannot load analytics', () => {
  for (const choice of ['', 'essential']) {
    const page = boot({ choice, stored: {
      marketshelper_cookie_notice: 'all',
      [storageKey]: JSON.stringify({ choice: 'analytics', version: 2 }),
    } });
    assert.equal(page.scripts.length, 0);
    assert.deepEqual(page.events(), []);
    assert.deepEqual(page.requests, []);
  }
});

test('current analytics choice initializes and records a hit exactly once with restricted settings', async () => {
  // data-choice is supplied by the server after it verifies the signed cookie;
  // JavaScript never grants consent by reading an unsigned browser value.
  const page = boot({ choice: 'analytics' });
  assert.equal(page.scripts.length, 1);
  assert.equal(page.scripts[0].src, 'https://mc.yandex.ru/metrika/tag.js');
  assert.equal(page.scripts[0].async, true);
  assert.deepEqual(page.events(), [
    [12345678, 'init', { defer: true, webvisor: false, clickmap: false, trackLinks: false }],
    [12345678, 'hit', 'https://marketshelper.test/', { referer: '' }],
  ]);
  page.click('cookie-settings-open');
  assert.equal(page.nodes['cookie-notice'].hidden, false);
  assert.equal(page.nodes['cookie-decline'].focused, true);
  await page.click('cookie-accept');
  await page.click('cookie-accept');
  assert.equal(page.scripts.length, 1);
  assert.equal(page.events().filter(event => event[1] === 'init').length, 1);
  assert.equal(page.events().filter(event => event[1] === 'hit').length, 1);
});

test('hit includes only safe normalized campaign labels and omits raw query, secrets and fragment', () => {
  const page = boot({
    choice: 'analytics',
    href: 'https://marketshelper.test/calculator?utm_source=Yandex&utm_medium=CPC&utm_campaign=wb_production_1'
      + '&utm_content=sk-private-example&utm_term=person%40example.test&email=private%40example.test&token=secret-value&arbitrary=raw'
      + '#access_token=fragment-secret',
  });
  const hit = page.events().find(event => event[1] === 'hit');
  assert.equal(hit[2], 'https://marketshelper.test/calculator?utm_source=yandex&utm_medium=cpc&utm_campaign=wb_production_1');
  assert.deepEqual(hit[3], { referer: '' });
  const serialized = JSON.stringify(page.events());
  for (const forbidden of ['private', 'person', 'email', 'token', 'secret', 'fragment', 'arbitrary', 'utm_content', 'utm_term', '#']) {
    assert.equal(serialized.includes(forbidden), false, `hit leaked ${forbidden}`);
  }
});

test('private page with an empty counter never loads a tracker even after analytics consent', async () => {
  const page = boot({ choice: 'analytics', counter: '', href: 'https://marketshelper.test/register?email=private%40example.test' });
  assert.equal(page.scripts.length, 0);
  await page.click('cookie-accept');
  assert.equal(page.scripts.length, 0);
  assert.deepEqual(page.events(), []);
});

test('accept starts analytics only after successful same-origin POST and suppresses double clicks', async () => {
  const pending = deferred();
  const page = boot({ respond: () => pending.promise });
  const saving = page.click('cookie-accept');
  await page.click('cookie-accept');
  assert.equal(page.requests.length, 1);
  assert.equal(page.scripts.length, 0);
  assert.equal(page.storage.has(storageKey), false);
  assert.equal(page.requests[0].url, '/privacy/analytics-consent');
  assert.equal(page.requests[0].options.method, 'POST');
  assert.equal(page.requests[0].options.credentials, 'same-origin');
  assert.deepEqual(page.requests[0].options.headers, { 'Content-Type': 'application/json' });
  assert.equal(JSON.parse(page.requests[0].options.body).choice, 'analytics');
  pending.resolve({ ok: true });
  await saving;
  assert.equal(page.scripts.length, 1);
  assert.equal(page.nodes['cookie-notice'].hidden, true);
  assert.deepEqual(JSON.parse(page.storage.get(storageKey)), { choice: 'analytics', version: 2 });
});

test('failed or rejected consent POST does not start analytics or replace previous choice', async () => {
  for (const respond of [() => Promise.resolve({ ok: false }), () => Promise.reject(new Error('simulated offline'))]) {
    const previous = JSON.stringify({ choice: 'essential', version: 2 });
    const page = boot({ choice: 'essential', stored: { [storageKey]: previous }, respond });
    page.click('cookie-settings-open');
    await page.click('cookie-accept');
    assert.equal(page.scripts.length, 0);
    assert.deepEqual(page.events(), []);
    assert.equal(page.storage.get(storageKey), previous);
    assert.equal(page.nodes['cookie-notice'].hidden, false);
    assert.match(page.nodes['cookie-status'].textContent, /Не удалось сохранить выбор/);
    assert.equal(page.reloads(), 0);
  }
});

test('successful essential POST destroys an initialized tracker and reloads only after acknowledgement', async () => {
  const pending = deferred();
  const page = boot({ choice: 'analytics', respond: () => pending.promise });
  const saving = page.click('cookie-decline');
  assert.equal(page.reloads(), 0);
  assert.equal(page.events().some(event => event[1] === 'destruct'), false);
  assert.deepEqual(JSON.parse(page.requests[0].options.body), { choice: 'essential' });
  pending.resolve({ ok: true });
  await saving;
  assert.deepEqual(page.events().at(-1), [12345678, 'destruct']);
  assert.equal(page.reloads(), 1);
  assert.deepEqual(JSON.parse(page.storage.get(storageKey)), { choice: 'essential', version: 2 });
});

test('unavailable optional analytics uses essential-only local choice without any network request', async () => {
  const page = boot({ available: false, counter: '' });
  await page.click('cookie-accept');
  await page.click('cookie-decline');
  assert.deepEqual(page.requests, []);
  assert.deepEqual(page.events(), []);
  assert.equal(page.scripts.length, 0);
  assert.equal(page.reloads(), 0);
  assert.equal(page.nodes['cookie-notice'].hidden, true);
  assert.deepEqual(JSON.parse(page.storage.get(storageKey)), { choice: 'essential', version: 2 });
});
