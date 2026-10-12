# Продолжение мобильной проверки — 12 октября 2026

Claude сообщил результат проверки `91dcab0`, ветка `claude/mobile-verify-20261013`, без новых коммитов и изменений исходников. Bundle/mobile `c8a0a55` уже входит в эту основу и в текущий `3933d2d`; дополнительного слияния этой проверочной ветки нет.

## Взято в работу

- Gateway теперь получает из Compose `WB_SAAS_ANDROID_PACKAGE`, `WB_SAAS_ANDROID_SHA256`, `WB_SAAS_IOS_APP_ID`. `.env.example` содержит пустые поля; действительные сертификаты/Apple application identifier не придуманы.
- Настроенный AASA смешивал современное `appIDs` с legacy `paths`. Исправлен на поддерживаемый `appID` + `paths`, подтверждено Apple TN3155 и независимым ревью. Список путей подтверждения email/сброса пароля/приглашения команды сохранён; пустые ответы и Android не менялись.
- README исправлен: `src/remote-shell.js → www/remote-shell.js`; описано, какие значения нужны и что `.env` не обновляет уже запущенный контейнер без пересоздания.

## Свежие проверки Codex

- Gateway: 591 passed, 215 subtests, без skipped в данном окружении. Ранее недоступные Docker-интеграции в этом прогоне доступны; это не тест боевого Postgres.
- Мобильные gateway-тесты: 13 passed независимо; regression AASA сначала воспроизвёл KeyError отсутствующего appID, после исправления проходит.
- Frontend app-shell-remote/download/MarketplaceBoundary: 12 passed (свежий отдельный прогон текущего HEAD; число18 из отчёта Claude не переносится как результат нашего прогона).
- Настоящий Docker Compose корректно передаёт все3значения при пустой и синтетической заполненной конфигурации; тестовые значения не используются на production.
- Три копии remote shell побайтово идентичны. Изменение не касается авторизации, CSP, финансов или tenant frontend/backend.

## Внешние этапы

Claude не собрал debug APK: JDK, Android SDK/adb/эмулятор не предоставлены. Настоящий вход в WebView, tenant redirect, скачивание/возврат в приложение и push не проверены. Реальные ключи подписи, SHA256 App Signing установленной Android-сборки, Apple Application Identifier Prefix/Bundle ID и доступ к Xcode/сторам ещё требуются. Пустые association-файлы — корректный незаполненный ответ, а не работоспособность App Links.

Общий admission budget сервера в этом мобильном отчёте не сделан; соответствующий пункт `AUDIT_CURRENT_STATUS_2026-10-12.md` остаётся открытым.

Источники: [Apple TN3155](https://developer.apple.com/documentation/technotes/tn3155-debugging-universal-links/), [Google Play App Signing](https://support.google.com/googleplay/android-developer/answer/9842756).

Production-подтверждение gateway/config rollout фиксируется в операторском акте `deliveries/mobile-link-config-20261012/RESULT.md`. Остальные пять кабинетов и provisioner не требуется пересоздавать для этих переменных.
