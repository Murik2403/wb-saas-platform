# Staging на Timeweb

Новая версия разворачивается отдельно от production-сайта, но проходит через уже существующий gateway. Рекомендуемый адрес тестового tenant: `redesign.app.marketshelper.ru`.

В проекте Claude уже есть login gateway с cookie `wb_saas_session` и Traefik ForwardAuth. Он проверяет доступ пользователя и соответствие tenant slug. Отдельный login экран внутри redesign для staging не нужен.

## Локальная проверка production-сборки

```bash
copy .env.staging.example .env.production
npm run build
npm run preview -- --host 0.0.0.0 --port 4177
npm run qa:staging -- http://127.0.0.1:4177
```

## Схема на сервере

- `redesign.app.marketshelper.ru` отдаёт содержимое `dist/` новой версии.
- `/tenant-api/` проксируется на локальный dashboard bridge или production API.
- основной `marketshelper.ru` и Streamlit-процесс остаются без изменений.

В production bridge должен использовать `MH_TENANT_SOURCE=host`: tenant берётся из host gateway-маршрута, а не из заголовка браузера. Заголовочный режим оставлен только для локальной разработки.

## Сборка отдельного tenant-образа

Для реального staging используется отдельный tenant-образ. Он собирается из текущего
`tenant-app` без изменения его файлов, запускает FastAPI на внутреннем `8001`
и React/Nginx на `8501`. Старый Streamlit-контейнер сохраняется отдельно как rollback:

```bash
docker build \
  --build-arg VITE_MH_API_MODE=v1 \
  -f mh-redesign-lab/Dockerfile.tenant-staging \
  -t wb-dashboard-tenant:redesign .
```

Запуск на сервере выполняется только для тестового tenant через отдельный compose-файл:

```bash
docker compose -f mh-redesign-lab/deploy/docker-compose.redesign-tenant.yml up -d
docker inspect wb-tenant-redesign --format '{{json .State.Health}}'
```

Этот профиль не меняет `WB_SAAS_TENANT_IMAGE`, не трогает существующие tenant-контейнеры
и не публикует порт на хост. Traefik видит контейнер через общий `wbsaas_net`.
Откат: `docker compose -f mh-redesign-lab/deploy/docker-compose.redesign-tenant.yml down`.

Для конкретного боевого tenant `mvvorokov` используется профиль
`deploy/docker-compose.mvvorokov-staging.yml`: он сохраняет тот же volume и
подключает точный production host `mvvorokov.app.marketshelper.ru`.

Готовый пример reverse proxy лежит в `deploy/nginx.marketshelper-staging.conf`.

## Перед переключением домена

1. Проверить `npm run qa:staging -- https://new.marketshelper.ru`.
2. Проверить все восемь `?view=` маршрутов на desktop и mobile.
3. Проверить создание и смену статуса action plan.
4. Снять резервную копию текущей конфигурации proxy.
5. Переключать основной домен только после отдельного согласования.

В production нельзя оставлять local-dev session и файловый action-plan store: session должен приходить от существующего gateway, а action plan нужно перенести в tenant API с SQLite/PostgreSQL.
