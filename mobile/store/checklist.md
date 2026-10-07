# Чек-лист: от сборки до публикации

Секреты (keystore, пароли, ключи) в репозиторий не кладём: `*.keystore`, `*.jks`, `google-services.json`, `GoogleService-Info.plist` в `mobile/.gitignore`.

## 0. Предусловия на сайте (блокеры)
- [ ] `https://marketshelper.ru/delete-account` работает (в репозитории gateway такой страницы сейчас нет). Нужна всем трём сторам.
- [ ] Из кабинета есть путь к удалению аккаунта (для Apple 5.1.1(v)).
- [ ] Gateway отдаёт `/.well-known/assetlinks.json` (Android App Links) и `/.well-known/apple-app-site-association` (iOS Universal Links), см. п. 2 и 5.
- [ ] Gateway отдаёт `shell.js` для удалённой страницы (см. README, «Remote shell script»), иначе back-кнопка/внешние ссылки/deep links сработают только на офлайн-экране.
- [ ] Аккаунт ревьюера: создать и подтвердить email вручную, кабинет на демо-данных с действующим доступом (trial/active), пароль задать самостоятельно (`<REVIEWER_PASSWORD>`) и **сменить после проверки**. Рекомендуется отдельный адрес вида review@marketshelper.ru вместо личной почты (сейчас в `review-notes.md`: mvvorokov@yandex.ru).
- [ ] В приложении нет кнопок оплаты (Apple 3.1.1).
- [ ] Контакт поддержки `<SUPPORT_EMAIL>`.

## 1. Сборка (Android)
- [ ] Установить JDK 21 и Android Studio (SDK 35+), `ANDROID_HOME`.
- [ ] `cd mobile && npm install && npm run sync`
- [ ] Debug: `cd android && ./gradlew assembleDebug` (APK в `android/app/build/outputs/apk/debug/`).
- [ ] Версия: `android/app/build.gradle` (`versionCode`, `versionName`); `versionCode` растёт с каждой загрузкой.

## 2. Подпись Android
- [ ] Создать upload keystore: `keytool -genkeypair -v -keystore marketshelper-upload.keystore -alias marketshelper -keyalg RSA -keysize 2048 -validity 10000` (пароли задаёт владелец, хранить в менеджере паролей, сделать резервную копию).
- [ ] Подключить подпись через `android/keystore.properties` (не коммитить) или Android Studio: Build > Generate Signed Bundle.
- [ ] Release AAB: `./gradlew bundleRelease`.
- [ ] Отпечаток SHA-256: `keytool -list -v -keystore marketshelper-upload.keystore -alias marketshelper` и, если Play App Signing включён, SHA-256 из Play Console (Setup > App signing). Для App Links нужен именно SHA-256 ключа подписи приложения в Play.
- [ ] Положить отпечаток в переменную `WB_SAAS_ANDROID_SHA256` и отдавать в `https://marketshelper.ru/.well-known/assetlinks.json`:
  ```json
  [{"relation":["delegate_permission/common.handle_all_urls"],
    "target":{"namespace":"android_app","package_name":"ru.marketshelper.app",
              "sha256_cert_fingerprints":["<WB_SAAS_ANDROID_SHA256>"]}}]
  ```
  Значение подставляет владелец (не выдумывать). Если указать оба (upload и Play), список можно расширить.

## 3. Google Play
- [ ] Аккаунт разработчика Play Console (разовый взнос 25 USD; для организаций нужен D-U-N-S; для личных аккаунтов новых разработчиков действует обязательное закрытое тестирование: 12+ тестировщиков 14 дней перед выходом в прод; проверьте актуальные правила).
- [ ] Создать приложение, пакет `ru.marketshelper.app`.
- [ ] Заполнить: карточка (`listing-ru.md`), иконка 512, feature graphic 1024x500 (нужно сделать: в проекте нет), скриншоты (`screenshots/android`), политика, Data safety (`data-safety-google.md`), Content rating, Target audience 18+, App access (`review-notes.md`), Account deletion URL.
- [ ] Загрузить AAB в Internal testing, затем Closed/Production.
- [ ] Push: создать проект Firebase, добавить Android-приложение, скачать `google-services.json` в `mobile/android/app/` (не коммитить), серверный ключ FCM (service account) хранить на сервере; см. README.

## 4. RuStore
- [ ] Аккаунт разработчика RuStore, подтверждение личности/юрлица.
- [ ] Загрузить подписанный APK/AAB, карточка и скриншоты (`rustore.md`).
- [ ] Push: RuStore Push (`projectId`, сервисный токен) при необходимости.
- [ ] Модерация обычно занимает дни; приложить доступ ревьюера.

## 5. iOS (нужен Mac с Xcode)
- [ ] Apple Developer Program (99 USD/год; для организации нужен D-U-N-S; из РФ оформление оплаты может быть заблокировано: частая причина невозможности публикации).
- [ ] На Mac: `cd mobile && npm install && npm run sync`, `npx cap open ios`.
- [ ] Xcode: Team, Bundle Identifier `ru.marketshelper.app`, Version/Build, `ITSAppUsesNonExemptEncryption=NO`.
- [ ] Capabilities: Push Notifications, Associated Domains (`applinks:marketshelper.ru`), Background Modes > Remote notifications (если нужны).
- [ ] Universal Links: отдавать `/.well-known/apple-app-site-association` (Team ID `<APPLE_TEAM_ID>`, подставляет владелец) с путями `/verify-email*`, `/reset-password*`, `/team/accept*`.
- [ ] APNs: ключ Auth Key (.p8) в Apple Developer > Keys; Key ID и Team ID использовать на сервере push (хранить вне репозитория).
- [ ] Product > Archive > Distribute > App Store Connect (TestFlight сначала).
- [ ] App Store Connect: карточка, скриншоты 6.7" (`screenshots/ios`, 1290x2796), Privacy (`app-privacy-apple.md`), review notes (`review-notes.md`), Age rating, Export compliance.

## 6. Что может заблокировать
- Нет страницы удаления аккаунта (все сторы).
- Apple 4.2 (обёртка над сайтом) и 3.1.1 (платежи вне IAP).
- Apple Developer / Google Play: оплата и верификация из РФ.
- Не работающий вход ревьюера (2FA, капча, не подтверждён email, нет подписки).
- Несоответствие Data safety / App Privacy политике.
- Тёмная тема как единственная в UI: не блокер, но проверить контраст.
- Не настроены assetlinks / AASA: ссылки откроются в браузере, а не в приложении (не блокер).

## 7. Публикация
- [ ] Internal/TestFlight тест на реальном устройстве: вход, редирект на кабинет, back-кнопка, внешняя ссылка (WB/Ozon), офлайн-экран, deep link из письма.
- [ ] Отправить на проверку, после одобрения выпустить релиз (поэтапный rollout 10-20%).
