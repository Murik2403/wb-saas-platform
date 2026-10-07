# P2 — мобильные приложения, PWA и публичные документы

Обследование исходников 07.10.2026, текущая база `ff90045` (после P1).
Продакшен, кабинеты разработчика и платёжные настройки в этом обследовании не изменялись.
Подготовленные изменения PWA ниже пока не закоммичены и не выложены.

## Проверенный остаток

| Задача | Подтверждённое состояние | Что остаётся |
|---|---|---|
| Нативная оболочка | В tracked tree нет Capacitor-конфига, Android/iOS-проекта и native-зависимостей. Есть план `docs/superpowers/plans/2026-09-10-full-product-mobile-release.md` и web PWA. | Отдельный проект оболочки и прототип входа gateway → tenant с проверкой cookies/origins; сборки и реальные устройства. |
| Store mode / платежи | `src/App.tsx:1036` ведёт в `/billing`; `/account` также показывает оплату. Отдельного store mode и навигационного блокировщика нет. | Выбор модели распространения/оплаты, вариант сборки и проверка всех прямых/косвенных переходов; скрытая ссылка сама по себе не является блокировкой. |
| App Links / Universal Links | Нет `assetlinks.json`, AASA и app-link обработчиков. Provisioner публикует только manifest/sw/offline/icons. Web verify/reset/team ссылки существуют. | Подписанные идентификаторы пакетов, Android SHA-256, Apple Team ID; точечная публичная выдача association-файлов без ForwardAuth, маршрутизация без токенов в логах. |
| Native downloads | `selected-shop.ts:138`, `data-source.ts:894,1287` и панели используют Blob/ObjectURL + anchor download. Native Filesystem/Share bridge отсутствует. | Общий адаптер web/native, share sheet и очистка временных файлов; реальные iOS/Android acceptance. Поведение WKWebView не проверено, установленной оболочки нет. |
| Push | Нет APNs/FCM/RuStore token registration, device-token storage и push transport. Telegram доставка отдельна от push. | Provider/project credentials, согласие и отзыв подписки, отдельные устройства/права, отправка без финансовых деталей в уведомлении. |
| Устройство и сессии | Сессии gateway имеют cookie и срок, но нет реестра устройств/native token revocation. | UI списка устройств/отзыва и безопасное native-хранилище после выбора архитектуры. |
| Удаление аккаунта | `/account/delete` проверяет сессию, CSRF, пароль и слово УДАЛИТЬ; удаляет tenant с volume и затем account. Меню ещё описывает аккаунт как «Пароль и email». | Публичная инструкция без входа, явная ссылка в аккаунте/приложении, точное описание retention и длительности удаления. |
| Сохранность платежей при удалении | `accounts.delete_account` выполняет DELETE accounts; payments/refunds имеют ON DELETE CASCADE в SQLite и PG схемах. Synthetic temp DB: платежей до удаления 1, после 0. Политика §5 обещает хранение юридически значимых записей. | Решить минимальный состав/срок хранения с владельцем, разработать отдельный неизменяемый/псевдонимизированный ledger и миграцию. Не менять прод финансовые записи наугад. |
| Отмена рекуррента | После DELETE account будущие `accounts_due_for_recurring_charge` его не найдут. В delete route нет отдельного cancellation этапа до удаления tenant. `cancel_account` меняет status на canceled. | Проверить гонку уже выбранного billing job/ожидающего webhook, закрыть выдачу новых списаний до долгой процедуры удаления; provider integration в sandbox. Автоматический возврат не обещан. |
| Аккаунт ревьюера | Есть публичный demo и синтетические данные, но отдельный постоянный подтверждённый reviewer account/инструкция в store console не найден. | Владелец задаёт безопасный способ ревью; исключить доступ к mvvorokov и реальные API writes. |
| PWA metadata / иконки | Есть manifest, PNG192/512, SVG и offline.html. Manifest без id/screenshots, orientation portrait-primary, purpose any maskable объединён; apple-touch использует192. | Отдельная проверенная maskable512, touch180, iOS1024 без альфы, Android adaptive assets, synthetic screenshots и orientation policy. Растровые поля не проверялись визуально. |
| Метрика / privacy | `src/metrika.ts` загружает tracker для разрешённых целей через analytics-config, включая tenant origin. Privacy §7.4 утверждает, что счётчик не подключается к кабинету. Ozon не назван; Telegram описан только как доставка отчётов. | Владелец/юрист согласует точный текст по реально включённым источникам, поддержке и мобильным данным. Не приписывать Ozon/Telegram функции, которых нет. |
| Оферта | §4.3 говорит о прекращении доступа после отмены; текущий cancel_account также ставит canceled. Тексты «отмена продления» в других местах требуют согласования с этим поведением. | Выбрать единое обещание доступа до конца периода или немедленного прекращения и правила возврата; затем менять код/тексты согласованно. |
| Compliance / чеки | `compliance_status.json` содержит72часа и внутренние next_steps; `/compliance` показывает этот контекст. RECEIPT_ENABLED default0, боевое значение не проверялось. | Проверить сроки/публичный текст с ответственным; скрыть внутренний checklist за admin, опубликовать подтверждённый статус. Владелец проверяет чеки и РКН; source default не доказывает отключение на проде. |

## Ограниченный PWA-пакет, подготовленный локально

Owned files: `mh-redesign-lab/public/sw.js`, `src/main.tsx`, новые
`src/pwa-updates.ts/.css/.test.ts`, `scripts/test-pwa-service-worker.mjs`.

- Только известные public bootstrap/icons/assets; API, приватные произвольные
  `.png/.js` download paths и URL с query не кэшируются.
- Только успешные basic, без redirect и без private/no-store.
- Чистка только своего cache prefix; один предыдущий shell остаётся для старых
  вкладок. Runtime assets ограничены64 на текущий shell.
- Нет автоматического skipWaiting или reload. Баннер предлагает сначала
  сохранить ввод; «Позже» оставляет его. Активация только по нажатию пользователя.
- Изменение committed build-id проверяется каждые5мин только в видимой вкладке,
  поэтому баннер появляется даже когда sw.js не менялся. Offline/login HTML не
  выдаётся за новую версию.
- Тесты5/5, отдельный Node VM service-worker regression, tsc и production build
  прошли. Реальные установленные PWA и native устройства не проверены.

## Что можно сделать без внешних аккаунтов

PWA-пакет выше; метаданные/иконки на synthetic материалах; native download adapter
как код и mocked tests; минимальная оболочка без публикации; строго ограниченные
association endpoint skeletons без выдуманных IDs; публичная инструкция удаления
по подтверждённому процессу; sandbox retention tests/migration proposal.
Store mode можно проектировать как отдельную сборку, но финальная payment policy
зависит от платформы, региона и модели продаж.

## Внешние предпосылки

Developer accounts и права на них, signing certificates/package IDs, Apple Team
ID и Android signing fingerprint, APNs/FCM/RuStore проекты, macOS/Xcode для iOS,
Android SDK/device acceptance, payment-model решение владельца, legal approval
privacy/offer/retention/РКН и подтверждение live receipt configuration.
Не устанавливались native пакеты, не создавались аккаунты, ключи и платежи.

## Первичные источники (проверены07.10.2026)

- [Apple App Review Guidelines](https://developer.apple.com/app-store/review/guidelines/): требования ревью/оплаты оцениваются по модели и региону; скрытие ссылок не гарантирует принятие.
- [Apple — account deletion](https://developer.apple.com/support/offering-account-deletion-in-your-app/).
- [Google — app account deletion](https://support.google.com/googleplay/android-developer/answer/13327111?hl=en): доступная процедура внутри приложения и внешний web resource.
- [Capacitor configuration](https://capacitorjs.com/docs/config): server.url/allowNavigation описываются как development/live-reload настройки, не готовый production deployment recipe. Рекомендацию удалённого gateway origin в исходном аудите требуется отдельно проверить.
