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

- Commit: `11811e19a11927b1960128fc995eed2b056f0837`.
- Gateway image: `sha256:37060137aeaf561b1ca2e5460aea0a3d142ad1f760926e8d3100fe72787d2b18`.
- Сервис переключён через image-only override; tenant-контейнеры и конфигурация
  production не менялись.
- Rollback tag сохранён на сервере как
  `mh-gateway:rollback-before-11811e19a11927b1960128fc995eed2b056f0837`.

## Проверка

- Gateway health: `healthy`.
- `/healthz`, `/robots.txt`, `/sitemap.xml`: HTTP 200.
- Public SEO audit: `396 pass, 0 warn, 0 fail`; 20 URL в sitemap.
- Тесты gateway mailer/trial/growth: `70 passed`, `14 subtests passed`.
