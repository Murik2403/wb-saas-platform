# Production release: email delivery state

Дата выпуска: 22 сентября 2026 года.

## Изменение

Gateway теперь различает три состояния SMTP-транспорта:

- `accepted` — SMTP-сервер принял сообщение;
- `failed` — передача завершилась ошибкой;
- `disabled` — SMTP не настроен, передача не выполнялась.

Состояние `disabled` не блокирует повторную попытку позже. Состояния
`accepted` и `failed` остаются зафиксированными, чтобы не создавать риск
дублирования писем.

## Выпуск

- Commit: `77932793993416b73a84cb52deaef36c7027c47f`.
- Gateway image: `sha256:24e81e87dca5006eaf179c04120789073443146c65d97849cb642ab73b4d19fa`.
- Сервис переключён через image-only override; tenant-контейнеры и конфигурация
  production не менялись.
- Rollback tag сохранён на сервере как
  `mh-gateway:rollback-before-77932793993416b73a84cb52deaef36c7027c47f`.

## Проверка

- Gateway health: `healthy`.
- `/healthz`, `/robots.txt`, `/sitemap.xml`: HTTP 200.
- Public SEO audit: `396 pass, 0 warn, 0 fail`; 20 URL в sitemap.
- Тесты gateway mailer/trial/growth: `70 passed`, `14 subtests passed`.
