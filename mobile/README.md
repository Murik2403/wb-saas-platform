# MARKETSHELPER mobile (Capacitor)

Обёртка над веб-кабинетом: WebView открывает `https://marketshelper.ru/login`, после входа gateway редиректит на `https://{slug}.app.marketshelper.ru`. Android и iOS, appId `ru.marketshelper.app`.

## Структура
- `app.json` — единственное место для appId, appName, URL входа, User-Agent суффикса и списка разрешённых доменов (читается `capacitor.config.ts`).
- `capacitor.config.ts` — `server.url`, `allowNavigation`, `cleartext: false`, `errorPath: index.html`, splash/status bar/keyboard.
- `www/index.html` — офлайн-экран «Нет соединения» (кнопка «Повторить», тёмная/светлая тема). Показывается при ошибке загрузки `server.url`.
- `src/shell.ts` -> `www/shell.js` — хуки: внешние ссылки в системном браузере, Android back, deep links (`/verify-email*`, `/reset-password*`, `/team/accept*`), status bar по теме, splash.
- `assets/` — исходники иконок и заставки; `scripts/make-assets.mjs` строит их из `mh-redesign-lab/public/icons/marketshelper.svg`.
- `android/`, `ios/` — сгенерированные нативные проекты (иконки и заставки уже сгенерированы).
- `store/` — материалы для сторов и скриншоты, `store/checklist.md` — путь до публикации.

## Установка и проверка
```bash
cd mobile
npm install
npm run typecheck      # tsc --noEmit
npm run build          # src/shell.ts -> www/shell.js
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

## Remote shell script (важно)
Приложение грузит удалённый сайт, поэтому `www/shell.js` на нём не выполняется сам. Нативный мост Capacitor в удалённую страницу внедряется (`window.Capacitor.Plugins`), но хуки должны запуститься на странице. Нужно, чтобы gateway отдавал `shell.js` (например `/static/app-shell.js`) и подключал его тегом `<script>` только когда User-Agent содержит `MarketsHelperApp`. Без этого работают только нативные умолчания (back по истории WebView, deep link открывает приложение на `server.url`). Gateway в рамках этой задачи не менялся.

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
Из мок-режима фронта (демо-данные, без реальных клиентов):
```bash
cd ../mh-redesign-lab && VITE_MH_API_MODE=mock npx vite --port 5350   # в другом терминале
cd ../mobile && node scripts/store-shots.mjs
```
Результат: `store/screenshots/android` (1080x1920) и `store/screenshots/ios` (1290x2796). Нужен Edge/Chrome (`EDGE_PATH`). Кабинет сейчас тёмный (светлой темы в этой ветке нет). Экран «Товары» в mock-режиме показывает нули: перед подачей заменить на реальный демо-аккаунт.

## Push-уведомления
Плагин `@capacitor/push-notifications` подключён, регистрация токена в коде не вызывается.
1. Android/FCM: проект Firebase, `google-services.json` в `android/app/` (не коммитить); применить плагин `com.google.gms.google-services` (Capacitor подключает его автоматически, если файл найден).
2. iOS/APNs: capability Push Notifications, ключ `.p8` на сервере.
3. RuStore: отдельный SDK RuStore Push (на устройствах без GMS FCM не работает).
4. На странице кабинета вызвать `Capacitor.Plugins.PushNotifications.requestPermissions()` и `register()`, токен отправить на сервер (эндпоинт нужно добавить в gateway). До этого push не работает.

## Нативные доработки, не сделанные автоматически
- iOS: Associated Domains, Push capability, `ITSAppUsesNonExemptEncryption` — в Xcode (см. `store/checklist.md`).
- Подпись релиза Android — своим keystore (`store/checklist.md`), секреты только через переменные и локальные файлы.
