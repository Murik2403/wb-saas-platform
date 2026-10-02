(function () {
  'use strict';
  var key = 'marketshelper-theme';
  function apply(theme) {
    var light = theme === 'light';
    document.documentElement.setAttribute('data-theme', light ? 'light' : 'dark');
    var meta = document.querySelector('meta[name="theme-color"]');
    if (meta) meta.setAttribute('content', light ? '#f6f7fb' : '#0a0b12');
    document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
      button.setAttribute('aria-pressed', light ? 'true' : 'false');
      button.setAttribute('aria-label', light ? 'Включить тёмную тему' : 'Включить светлую тему');
      button.title = light ? 'Включить тёмную тему' : 'Включить светлую тему';
      button.textContent = light ? '☾ Тёмная тема' : '☀ Светлая тема';
    });
  }
  var initial = 'dark';
  try { initial = window.localStorage.getItem(key); } catch (_) { /* Storage can be disabled. */ }
  apply(initial);
  document.addEventListener('DOMContentLoaded', function () {
    apply(document.documentElement.getAttribute('data-theme'));
    document.querySelectorAll('[data-theme-toggle]').forEach(function (button) {
      button.addEventListener('click', function () {
        var next = document.documentElement.getAttribute('data-theme') === 'light' ? 'dark' : 'light';
        apply(next);
        try { window.localStorage.setItem(key, next); } catch (_) { /* The current page still switches. */ }
      });
    });
  });
  window.addEventListener('storage', function (event) {
    if (event.key === key) apply(event.newValue);
  });
})();
