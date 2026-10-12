# Приёмка Android — 12 октября 2026

Исходная ревизия: `10266b7`. Эмулятор и тесты впервые запущены на этой машине; прежний блокер «нет JDK/SDK/adb» снят. Исправленная нативная сборка пока проверялась как явно обозначенный рабочий снимок; выдаваемый APK должен отдельно собираться из итогового закоммиченного HEAD.

## Установленное ПО и устройство

- JDK 21.0.12.1+1, Android SDK platform 36/build tools 35 и 36/platform-tools — акт `deliveries/android-audit-20261012`.
- Android Emulator 37.2.12.0, Google APIs x86_64 API 36 revision 7. Официальный архив: 1 895 447 397 байт, SHA-1 `c6bf44bdcd885bb902b4ba752d111a073ad7a817`, совпал с Google repository XML. Java SDK manager не скачал этот пакет; curl скачал тот же официальный архив с продолжением загрузки, контрольная сумма проверена перед распаковкой.
- `emulator -accel-check`: exit 0, WHPX(10.0.28020) installed and usable. Настройки Windows/BIOS, средства защиты и перезагрузка не менялись.
- Изолированный AVD `marketshelper_audit_20261012`: headless, 1536 MiB, 2 ядра, software GPU, без сохранения snapshot. Boot completed, API 36, Google WebView 133.0.6943.137. Публичный demo, без входа в владельца/клиентов.

## Реально обнаруженная и исправленная ошибка

Исходный APK открывает страницу входа с native bridge, но после перехода на `demo.app.marketshelper.ru` `window.Capacitor` отсутствует. Первый инструментальный запуск упал на этом утверждении; logcat подтвердил `undefined triggerEvent/Plugins`.

В Capacitor 8.5.3 `Bridge.loadWebView()` регистрирует document-start скрипт только для начального origin, затем отключает proxy-инъекцию. `allowNavigation` разрешает переход в кабинет, но не расширяет эту конкретную инъекцию.

`MainActivity` использует публичные `JSExport`, `PluginManager`, `getPlugin` и AndroidX `WebViewCompat.addDocumentStartJavaScript` для дополнительных доверенных origins: `https://*.app.marketshelper.ru`, `https://localhost`. Начальный origin повторно не регистрируется. В `app.json` убран излишне широкий `*.marketshelper.ru`; остаются вход и домены кабинетов. Добавлена прямая зависимость AndroidX WebKit той же уже используемой версии. Никаких reflection, правок node_modules или ослабления CSP.

Вторая ошибка подтверждена отдельным red/green: полный `/login` в `server.url` попадал в AndroidX origin rules. Недопустимый путь приводил `MessageHandler` к fallback `addJavascriptInterface` без origin-фильтра, и на контролируемой недоверенной странице оставался `window.androidBridge`. Конфигурация теперь разделяет `server.url = login.origin` и `server.appStartPath = /login`. Усиленное отрицательное утверждение проверяет отсутствие и `Capacitor`, и raw `androidBridge`; до правки оно упало, после прошло.

Для старых WebView без `WEB_MESSAGE_LISTENER`/`DOCUMENT_START_SCRIPT` (или с включённым legacy bridge) добавлен отказ до создания `Bridge`: показывается нативный экран «Обновите Android System WebView», remote URL не загружается и JS interface не создаётся. Lifecycle `BridgeActivity` проверен: он корректно обрабатывает null bridge. При неожиданном fallback уже на поддерживаемом WebView глобальный `JavascriptInterface` удаляется; scoped WebMessageListener остаётся. Debug-only неэкспортируемая activity принудительно проверяет ветку отказа без изменений глобальных features; в release этого entry point нет.

## Пройденные проверки

После исправления навигации: `OK (1 test)`, 24.937 секунды. После дополнительного исправления origin-фильтра: `OK (1 test)`, 10.7 секунды (тёмный режим AVD). Один последовательный инструментальный сценарий проверил:

1. Production `/login`: форма и UA `MarketsHelperApp/1.0`, native bridge и remote-shell.
2. Public demo finance: загрузка и native bridge после перехода на домен кабинета.
3. Нативный Android Back: возврат на страницу входа.
4. Filesystem: запись и чтение синтетического файла в cache с проверкой байтов/URI.
5. Browser: открытие официального публичного сайта и закрытие через нативный plugin, WebView сохранил свой домен.
6. Контролируемый недоверенный origin: отсутствуют и `window.Capacitor`, и raw `window.androidBridge`.
7. Запрет сети только внутри тестового WebView: локальный экран «Нет соединения» и CSS темы согласно `prefers-color-scheme`.
8. Восстановление сети: повторная загрузка формы входа.

После добавления fail-closed guard: `OK (2 tests)`, 31.597 секунды. Дополнительный независимый тест подтвердил нативное сообщение об обновлении, отсутствие созданного Bridge и корректную обработку configuration change/закрытия activity; предыдущий полный удалённый сценарий также прошёл.

Логи, официальный repository XML, контрольная сумма и harness: `deliveries/android-runtime-20261012`. В репозитории сохранён opt-in `RuntimeAuditTest`; команда в `mobile/README.md`. Без `auditRemote true` сетевой сценарий пропускается.

## Оставшиеся границы

- Не проверялся вход с настоящими credentials/SSO-cookie; тест явно не создаёт пользователей и не меняет кабинеты.
- Проверена запись/чтение файла native Filesystem; передача документа конкретному получателю через share sheet и пользовательский CSV/XLSX download не заявляются как завершённые.
- Настоящее физическое устройство, разные версии Android/WebView и release-подпись/сторы ещё не проверены.
- Push не готов: регистрация в приложении/серверный endpoint ещё отсутствуют, Firebase/APNs credentials не добавлялись. Наличие зависимости и разрешения не доказывает работающий push.
- iOS-сборка требует macOS/Xcode и аккаунта/подписи; Android эмулятор её не подтверждает.

## Первичные источники

- [Android Emulator acceleration](https://developer.android.com/studio/run/emulator-acceleration)
- [Command-line emulator](https://developer.android.com/studio/run/emulator-commandline)
- [Instrumented tests](https://developer.android.com/training/testing/instrumented-tests)
- [Capacitor 8.5.3 Bridge source](https://github.com/ionic-team/capacitor/blob/8.5.3/android/capacitor/src/main/java/com/getcapacitor/Bridge.java)
- [AndroidX WebViewCompat](https://developer.android.com/reference/androidx/webkit/WebViewCompat)
