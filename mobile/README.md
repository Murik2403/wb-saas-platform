# MARKETSHELPER mobile (Capacitor)

Обёртка над веб-кабинетом: WebView открывает `https://marketshelper.ru/login`, после входа gateway редиректит на `https://{slug}.app.marketshelper.ru`. Android и iOS, appId `ru.marketshelper.app`.

## Структура
- `app.json` — единственное место для appId, appName, URL входа, User-Agent суффикса и списка разрешённых доменов (читается `capacitor.config.ts`).
- `capacitor.config.ts` — `server.url`, `allowNavigation`, `cleartext: false`, `errorPath: index.html`, splash/status bar/keyboard.
- `www/index.html` — офлайн-экран «Нет соединения» (кнопка «Повторить», тёмная/светлая тема). Показывается при ошибке загрузки `server.url`.
- `src/remote-shell.js` -> `www/remote-shell.js` — хуки: внешние ссылки в системном браузере, Android back, deep links (`/verify-email*`, `/reset-password*`, `/team/accept*`), status bar по теме, splash.
- `assets/` — исходники иконок и заставки; `scripts/make-assets.mjs` строит их из `mh-redesign-lab/public/icons/marketshelper.svg`.
- `android/`, `ios/` — сгенерированные нативные проекты (иконки и заставки уже сгенерированы).
- `store/` — материалы для сторов и скриншоты, `store/checklist.md` — путь до публикации.

## Установка и проверка
```bash
cd mobile
npm install
npm run typecheck      # tsc --noEmit
npm run build          # src/remote-shell.js -> www/remote-shell.js
npx cap doctor
```

## Debug APK
Нужны JDK 21 и Android SDK (Android Studio).
```bash
npm run sync           # build + cap sync
cd android && ./gradlew assembleDebug
# APK: android/app/build/outputs/apk/debug/app-debug.apk
```
Или `npx cap open android` и Run на эмуляторе/устройстве. iOS (только macOS): `npx cap open ios`.

## Remote shell script
Приложение грузит удалённый сайт, поэтому хуки (внешние ссылки в системный браузер, кнопка «назад», deep links `/verify-email*`, `/reset-password*`, `/team/accept*`, status bar, splash) подключаются самой страницей. Канонический исходник: `src/remote-shell.js` (чистый ES2017, без зависимостей и сборки, использует только `window.Capacitor.Plugins`; вне приложения ничего не делает). Он же собирается в `www/remote-shell.js` для офлайн-экрана (`npm run build`).

Копии (должны быть идентичны, это проверяет `gateway/tests/test_mobile_gateway.py`):
- `gateway/static/app-shell.js`: gateway добавляет `<script src="/static/app-shell.js" defer>` в `base.html` только для User-Agent с `MarketsHelperApp/`;
- `mh-redesign-lab/public/app-shell.js`: подключён в `mh-redesign-lab/index.html`; тесты в `src/app-shell-remote.test.ts`.

После правки `src/remote-shell.js` скопируйте файл в оба места.

## Настройка ссылок приложения в gateway

В серверном `.env` задаются три необязательных значения, передаваемых в gateway через `docker-compose.yml`:

- `WB_SAAS_ANDROID_PACKAGE` — фактический applicationId Android.
- `WB_SAAS_ANDROID_SHA256` — SHA-256 сертификатов подписи, несколько отпечатков через запятую. Для приложения из Google Play нужен сертификат App Signing из Play Console; upload-сертификат сам по себе не подтверждает установленную Play-сборку.
- `WB_SAAS_IOS_APP_ID` — фактический `<ApplicationIdentifierPrefix>.<BundleIdentifier>` из подписанного приложения Apple Developer/Xcode; для обычной новой настройки префикс — Team ID.

Пока сертификаты/Team ID не предоставлены, оставьте значения пустыми. Gateway отдаёт пустые association-файлы; это не подтверждение работающих App Links/Universal Links. После заполнения нужна штатная проверенная пересборка конфигурации и пересоздание gateway: простое изменение `.env` не меняет environment уже работающего контейнера. Сам tenant и Traefik из-за этих значений пересоздавать не требуется.

Проверка: `/.well-known/assetlinks.json` и `/.well-known/apple-app-site-association`, затем переход по ссылке на реальном подписанном приложении. Debug APK и production Play-сборка могут иметь разные сертификаты. Ключи подписи и пароли в Git не добавляются.

AASA использует совместимый формат `appID` + `paths`, без смешивания с современным `appIDs` + `components`. Источники: [Apple TN3155](https://developer.apple.com/documentation/technotes/tn3155-debugging-universal-links/), [Google Play App Signing](https://support.google.com/googleplay/android-developer/answer/9842756).

## Смена appId
1. Поменять `appId` в `app.json`.
2. Android: `applicationId`/`namespace` в `android/app/build.gradle`, переместить пакет `MainActivity` (`android/app/src/main/java/...`) и `package` в нём. Проще удалить `android/` и выполнить `npx cap add android`, затем повторить правки манифеста (intent-filter App Links и `POST_NOTIFICATIONS`) и `npx capacitor-assets generate`.
3. iOS: Bundle Identifier в Xcode.
4. Обновить `assetlinks.json` / AASA и записи в консолях сторов.

## Иконки и заставка
```bash
npm run assets:source     # пересобрать assets/*.png и PWA maskable-иконку
npm run assets:generate   # размеры для android/ios
```

## Скриншоты для сторов
Светлая тема, данные демо-тенанта из `scripts/demo-cabinet.json` (без реальных клиентов): `/tenant-api/*` подменяется ответами снимка через CDP, Vite в обычном режиме (mock-режим API не вызывает):
```bash
cd ../mh-redesign-lab && npx vite --port 5360   # в другом терминале
cd ../mobile && node scripts/store-shots.mjs
```
Результат: `store/screenshots/android` (1080x1920) и `store/screenshots/ios` (1290x2796). Нужен Edge/Chrome (`EDGE_PATH`). Снимок сделан в начале суток, поэтому «Сегодня» заполняется данными предыдущего дня из того же снимка (см. `today()` в скрипте). «Отчёты» в снимке пусты (нет расписаний).

## Push-уведомления
Плагин `@capacitor/push-notifications` подключён, регистрация токена в коде не вызывается.
1. Android/FCM: проект Firebase, `google-services.json` в `android/app/` (не коммитить); применить плагин `com.google.gms.google-services` (Capacitor подключает его автоматически, если файл найден).
2. iOS/APNs: capability Push Notifications, ключ `.p8` на сервере.
3. RuStore: отдельный SDK RuStore Push (на устройствах без GMS FCM не работает).
4. На странице кабинета вызвать `Capacitor.Plugins.PushNotifications.requestPermissions()` и `register()`, токен отправить на сервер (эндпоинт нужно добавить в gateway). До этого push не работает.

## Нативные доработки, не сделанные автоматически
- iOS: Associated Domains, Push capability, `ITSAppUsesNonExemptEncryption` — в Xcode (см. `store/checklist.md`).
- Подпись релиза Android — своим keystore (`store/checklist.md`), секреты только через переменные и локальные файлы.

## Приёмка Android на эмуляторе

Нужны JDK 21, Android SDK API 36 и запущенное Android-устройство/AVD. После `npm run build` и `npx cap sync android`:

```bash
cd android
./gradlew :app:assembleDebug :app:assembleDebugAndroidTest
adb install -r app/build/outputs/apk/debug/app-debug.apk
adb install -r app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk
adb shell am instrument -w -r -e auditRemote true -e class ru.marketshelper.app.RuntimeAuditTest ru.marketshelper.app.test/androidx.test.runner.AndroidJUnitRunner
```

На Windows используйте `gradlew.bat`. Инструментальный тест включается только аргументом `auditRemote true`: он читает публичные страницы сайта/демо, проверяет нативный bridge после перехода на домен кабинета, системный Back, запись/чтение синтетического файла в cache, Browser, локальный экран ошибки и восстановление сети. Учётные данные и отправка файлов получателю не используются.

`MainActivity` дополнительно регистрирует bridge на `https://*.app.marketshelper.ru` и локальном `https://localhost`: Capacitor 8.5.3 регистрирует DOCUMENT_START_SCRIPT только для начального домена входа. На произвольные сайты bridge не устанавливается; отрицательная проверка входит в тест. Новый APK после этой правки обязателен: обновление сайта не меняет нативную оболочку.

`server.url` должен содержать только origin. Путь входа задаётся через `server.appStartPath`; иначе AndroidX отклоняет origin rules, а Capacitor переходит на небезопасный глобальный JavaScript interface. Тест проверяет отсутствие на недоверенном origin как JS facade, так и raw `androidBridge`.

WebView без безопасных document-start/message-listener APIs не открывает remote сайт: вместо него появляется нативное сообщение об обновлении Android System WebView. Legacy bridge отключён. Отдельный инструментальный тест отказа работает без доступа к сети и не требует `auditRemote`; его тестовая activity присутствует только в debug и не экспортируется.
