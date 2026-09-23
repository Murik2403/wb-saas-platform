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

## Последующее обновление того же выпуска

- Commit: `2bcebd7`.
- Gateway image: `sha256:bbfaa533a8cca9126b6244aa019ab0f557936a50fb3b5c49aae81c500c42700f`.
- Добавлено отдельное поле `marketing_consent_at`; согласие на обработку ПДн
  больше не запускает промо-цепочку.
- Регистрационная форма получила необязательный opt-in, отписка по-прежнему
  доступна в каждом письме.
- После переключения gateway снова `healthy`; полный gateway suite: `342 passed`,
  `2 skipped`, `146 subtests passed`.

## Исправление атрибуции кампании

- Commit: `4b85698`.
- `/vs-1c` добавлен в `PUBLIC_ANALYTICS_PATHS`, поэтому группа `accounting_01`
  не теряет Метрику и UTM-атрибуцию после согласия на аналитику.
- Production image: `sha256:6be8b9c94af6c134bae5ff1a6bd285f22eb713814214b76e08dd88237aac33e2`.
- Проверка: gateway `healthy`, `/vs-1c` HTTP 200, sitemap содержит 21 URL,
  SEO-аудит `432 pass, 0 warn, 0 fail`.
