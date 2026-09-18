# Gateway growth: проверка стенда и порядок выпуска

Проверено по исходникам 19 сентября 2026 года. Этот документ не означает, что production обновлён. Команды разделены на проверки без внешних операций и последующий выпуск. Изменения перенесены в основной проект; отдельная проверочная копия: `C:\Users\mvvor\Downloads\wbsaasplatform\marketshelper-promotion-stage1`.

## 1. Что входит в выпуск

- Gateway: `logic/growth.py`, три новые таблицы, регистрация/подтверждение email, события provisioning и billing, защищённый `POST /internal/growth/event`, аналитика публичных страниц и админка.
- Redesign tenant: `growth_activation.py`, `growth_delivery.py`, новые API-маршруты и компонент первого отчёта. Оба Python-модуля включены в `mh-redesign-lab/Dockerfile.tenant-staging`.
- `scripts/run_billing_cycle.py`: передача подтверждённого признака тестового платежа. Скрипт в Compose монтируется с хоста; обновление одного образа gateway не обновляет этот файл на хосте.
- `docker-compose.yml`: явная передача четырёх необязательных настроек аналитики/верификации. Файл `.env` сам по себе не передаёт их контейнеру.
- `mh-redesign-lab/deploy/docker-compose.redesign-tenant.yml`: явно принимает публичный HTTPS callback и scoped internal credential; пустой credential оставляет outbox отключённым до его безопасной настройки.

Обновление только gateway не доставит старым кабинетам новый первый отчёт и outbox. Обновление только tenant приведёт к недоставленным событиям, если gateway ещё не знает маршрут/таблицы. Порядок: проверенная резервная копия → gateway → один тестовый redesign tenant → остальные кабинеты после проверки.

## 2. Настройки и границы

| Настройка | Gateway / источник | Что проверить без печати значения |
|---|---|---|
| `WB_SAAS_INTERNAL_API_SECRET` | Существующий ключ gateway | Задан; не заменять ради smoke. Gateway выводит отдельный credential для каждого slug |
| `WB_SAAS_GROWTH_COOKIE_SECRET` | Необязательный отдельный ключ gateway | Передан через Compose; если пуст, применяется внутренний ключ с разделением назначения подписи. Если оба пусты, optional analytics выключена |
| `WB_SAAS_YANDEX_METRIKA_ID` | Необязательный ID | На offline-стенде пуст; в production включать только после проверки согласия |
| `WB_SAAS_YANDEX_VERIFICATION`, `WB_SAAS_GOOGLE_VERIFICATION` | Необязательная проверка домена | Передаются в gateway, не включают счётчик |
| `WB_SAAS_COOKIE_SECURE`, `WB_SAAS_PARENT_DOMAIN` | Gateway | Production: secure=1 и правильный домен; от них строится HTTPS callback tenant → gateway |
| `WB_SAAS_TENANT_IMAGE` | Gateway и provisioner | Оба указывают на один проверенный образ redesign. Provisioner использует свой allowlist; произвольный image из запроса не применяется |
| `WB_SAAS_ADMIN_EMAILS` | Gateway | Нужный оператор включён; эти аккаунты автоматически исключаются из growth KPI |
| `WB_SAAS_YOOKASSA_WEBHOOK_CIDRS` | Gateway | Для рабочего webhook нужен актуальный allowlist. В offline-проверке реальный webhook не вызывать |
| `WB_SAAS_DB_BACKEND`, `WB_SAAS_DATABASE_URL` | Gateway | Сверить фактический backend до/после выпуска; growth не требует смены БД |

В текущем базовом Compose последние две настройки БД не передаются. Для существующего Postgres-развёртывания должен сохраняться проверенный deployment override, передающий их; не удалять его при выпуске. Переключение SQLite → Postgres не включено в этот выпуск. Старую инструкцию `DEPLOY.md` о переключении нельзя считать безопасным переносом работающей БД без отдельного окна обслуживания, проверки переносчика и сверки данных.

Provisioner передаёт в каждый tenant `WB_SAAS_TENANT_SLUG`, `WB_SAAS_INTERNAL_API_URL` и выведенный для него scoped `WB_SAAS_INTERNAL_API_SECRET`. Standalone-профиль `redesign` использует отдельную переменную `WB_SAAS_REDESIGN_INTERNAL_API_SECRET`; master secret gateway туда подставлять нельзя. Tenant изолирован от Docker-сети gateway и обращается к публичному HTTPS-домену gateway. Поэтому `http://gateway:8000` сейчас не является правильной заменой. В локальном тесте допускается HTTP loopback. Перенаправления callback запрещены, ответ должен быть именно 204.

На стенде без внешних операций: нет SMTP host/user/password, ключей YooKassa, токенов WB/Ozon, Telegram bot token и Sentry DSN; ID Метрики пуст. Используются только отдельные искусственные аккаунты/базы. Не копировать боевой volume или реальные `.env`/токены на этот стенд. Реальный tenant entrypoint запускает синхронизацию WB, агента и планировщик отчётов — offline-проверку проводить тестами либо отдельным запуском только API с пустыми данными.

## 3. Воспроизводимый smoke без писем, WB и платежей

Сквозная проверка на двух настоящих ASGI-процессах, с временными SQLite-базами и вымышленными scoped credentials:

```powershell
python scripts/smoke_growth_flow.py
```

Нужны установленные Python-зависимости gateway и tenant. Скрипт сбрасывает унаследованные настройки приложения, использует случайные loopback-порты, блокирует внешние соединения и завершает только созданные им процессы. Рабочие базы и секреты не копируются. Ожидается JSON с `status: PASS`, `gateway_first_useful_report_count: 1`, тремя попытками доставки и сохранённым `original_occurred_at`.

Проверяется фактический расчёт первого отчёта из сохранённых вымышленных строк, отказ при ошибочной/частичной загрузке и нулевой стоимости, подтверждение с проверкой пользователя и версии отчёта, настоящий HTTP 403 на неверный scoped credential, сохранность очереди после перезапуска tenant, доставка в gateway и отсутствие дубля при повторе после имитации потерянного подтверждения. Для ускорения меняется только время последней попытки в тестовой очереди, а не время события. Скрипт не подтверждает реальную синхронизацию WB, почту, оплату или отображение отчёта в браузере.

Команды для PowerShell, из указанной рабочей копии. В этих тестах SMTP, provisioning и провайдер платежей заменены тестовыми ответами; базы временные. Никакого запуска `run_billing_cycle.py`, `POST /billing/checkout`, реального `/verify-email` или реального `/webhooks/yookassa` этот smoke не требует.

```powershell
Set-Location 'C:\Users\mvvor\Downloads\wbsaasplatform\marketshelper-promotion-stage1'
docker compose --env-file .env.example config --quiet
Push-Location gateway
python -m pytest tests/test_growth.py tests/test_growth_admin.py tests/test_growth_integration.py tests/test_growth_provisioning.py tests/test_provisioning_credentials.py tests/test_email_verification_routes.py tests/test_billing_routes_webhook.py -q
Pop-Location
node gateway/tests/test_public_analytics.cjs
Push-Location mh-redesign-lab/tenant-api
python -m pytest tests/test_growth_activation.py tests/test_growth_routes.py tests/test_growth_delivery.py -q
Pop-Location
```

У каждого вызова должен быть код выхода 0. Не продолжать после ошибки. Для независимого Postgres-smoke при уже работающем локальном Docker и имеющемся образе:

```powershell
docker image inspect postgres:16-alpine --format '{{.Id}}'
Push-Location gateway
python -m pytest tests/test_growth_postgres.py -q
Pop-Location
```

Это временный контейнер PostgreSQL с тестовыми данными, а не подключение к рабочей базе. `skipped` не является подтверждением Postgres. Если образа или Docker нет, этот пункт отмечается как непроверенный; для SQLite-выпуска не требуется установка Docker ради него.

Проверяемые результаты:

1. Регистрация создаёт аккаунт и `account_created`, но не начинает пробу. `captured_at`/`touch_at` — время перехода; время регистрации не берётся из cookie. Приглашённый сотрудник не становится новой организацией.
2. Подтверждение email даёт `email_verified` и ставит provisioning в очередь. Только успешный health-check даёт `tenant_ready` и `trial_started`; неудача не запускает пробу. Повтор не дублирует milestones.
3. GET готовности не записывает активацию. Нужны собственные данные выбранного SKU, успешная полная синхронизация, положительная конечная себестоимость и рассчитанный отчёт. POST подтверждения проверяет текущую версию отчёта и авторизованную роль.
4. Outbox сохраняет первое `occurred_at`; 204 отмечает доставку. Ошибка/не-204 оставляет запись для повторения не раньше чем через 60 секунд. Без следующего обращения фоновой гарантированной доставки пока нет.
5. Gateway отвергает credential другого tenant, demo, клиентский `account_id` и неправильное время. Повтор одной записи не повышает A7.
6. Успешный платёж берётся из проверенного ответа провайдера, не из POST body. Повтор не продлевает подписку второй раз. `test=true`, включая поздно пришедший признак, нулевые платежи и возвраты не входят в подтверждённые оплаты. Ручное продление не создаёт renewal.
7. A7/P30 включают только созревшие пробы; тестовые/служебные аккаунты исключены. Пустой знаменатель и неизвестные расходы показываются как отсутствие данных, CAC не равен нулю.

## 4. Безопасная проверка локального собранного gateway

Если контейнер `mh-promotion-gateway-review` уже запущен на `127.0.0.1:8097`, эти проверки только читают публичные страницы и схему. Команды не создают пользователей и не отправляют события:

```powershell
docker inspect mh-promotion-gateway-review --format '{{.State.Health.Status}} {{.Config.Image}}'
$smokeOrigin = 'http://127.0.0.1:8097'
foreach ($smokePath in @('/healthz', '/', '/pricing', '/dlya-proizvoditeley-wb', '/calculator', '/guides', '/robots.txt', '/sitemap.xml', '/static/public-analytics.js')) {
    $smokeResponse = Invoke-WebRequest -Uri ($smokeOrigin + $smokePath)
    [pscustomobject]@{ Path = $smokePath; Status = $smokeResponse.StatusCode }
}
@'
import config
from logic import db
print({
    'db_backend': config.DB_BACKEND,
    'internal_key_set': bool(config.INTERNAL_API_SECRET),
    'cookie_key_set': bool(config.GROWTH_COOKIE_SECRET),
    'metrika_set': bool(config.YANDEX_METRIKA_ID),
    'smtp_set': bool(config.SMTP_HOST),
    'payment_credentials_set': bool(config.YOOKASSA_SHOP_ID or config.YOOKASSA_SECRET_KEY),
})
with db.connect() as conn:
    for table in ('growth_sources', 'growth_events', 'growth_payment_exclusions'):
        conn.execute('SELECT 1 FROM ' + table + ' LIMIT 1').fetchall()
        print(table + ': readable')
'@ | docker exec -i mh-promotion-gateway-review python3 -
```

Ожидаются healthy, HTTP 200 и три readable. В offline-превью `metrika_set`, `smtp_set`, `payment_credentials_set` должны быть False. Булевы признаки не раскрывают ключи. Не заменять этот вывод на `docker inspect .Config.Env`, `env` или печать конфигурации: там могут быть секреты.

В браузере: до согласия нет загрузки счётчика; отказ сохраняется; без настроенного ID Метрики счётчик не загружается даже после разрешения. Проверка настоящего счётчика относится к отдельному разрешённому выпуску, а не к этому offline-smoke.

## 5. Миграции и выпуск на отдельном окружении

Gateway вызывает `db.init_db()` в lifespan при запуске. Изменение добавляет `growth_sources`, `growth_events`, `growth_payment_exclusions` и индекс; старым аккаунтам не выдумываются события. Tenant при запуске добавляет `growth_first_value` и `growth_event_outbox` в свою SQLite. Миграции проверены как повторяемые, включая временный Postgres; это не проверка боевого backup/restore.

До выпуска сохранить проверенную резервную копию control-plane и volume выбранного тестового tenant существующей процедурой. Для SQLite использовать согласованный snapshot/backup, учитывающий WAL, а не копирование одного открытого `.sqlite3` файла. Не менять backend и не переносить данные между БД в том же выпуске.

После разрешения выпуска на отдельном окружении:

1. Зафиксировать проверенный исходный commit и digest обоих образов. Собрать gateway и redesign tenant, а не legacy tenant. Путь сборки tenant: `docker build -f mh-redesign-lab/Dockerfile.tenant-staging ... .` из корня репозитория.
2. Применить Compose-передачу новых настроек, сохранив текущие overrides. Задать пустой ID Метрики до отдельной проверки consent. Не ротировать внутренний ключ: уже запущенные tenants используют credential, выведенный из прежнего ключа.
3. Обновить gateway и проверить `/healthz`, публичные страницы, `/admin` после входа администратора и наличие трёх таблиц. Миграции выполняются самим запуском; отдельно запускать `init_db` против production ради smoke не нужно.
4. Обновить **один отдельный тестовый tenant** через существующий проверенный механизм; сохранить его volume. Проверить совпадение configured image в gateway/provisioner и фактического image tenant. После обновления старые уже работающие кабинеты не получают выдуманный `trial_started`.
5. Проверить tenant `/healthz`, `/api/v1/session`, `/api/v1/growth/activation` и наличие двух таблиц в фактическом `config.DB_PATH`. У gateway-managed tenant данные находятся в `/app/data`, а не в случайном новом каталоге исходников. На пустых данных готовность должна быть false.
6. Положительную цепочку до отчёта/оплаты пока подтверждают изолированные тесты выше. Реальные email, WB и sandbox YooKassa требуют отдельного разрешённого сценария; они не считаются выполненными этим smoke.

Контракт внутренних маршрутов для расследования без отправки секретов:

| Маршрут | Ожидаемое поведение |
|---|---|
| `GET /api/v1/growth/activation?nm_id=<SKU>` | Читает готовность; может повторить доставку уже записанного outbox; не создаёт новую активацию |
| `GET /api/v1/growth/first-report?nm_id=<SKU>` | Возвращает расчёт с `report_revision` либо 409; не записывает просмотр |
| `POST /api/v1/growth/report-viewed` | Авторизованная роль + текущий `nm_id/report_revision`; повторная проверка, транзакция первого результата и outbox |
| `POST /internal/growth/event` | Только tenant credential; поля `slug`, `event_name=first_useful_report`, `event_key=first_useful_report`, `occurred_at`; успех 204 |
| `POST /webhooks/yookassa` | Allowlist источника + серверная проверка у провайдера; локальные позитивные smoke используют mock, не HTTP-запрос в работающий webhook |

## 6. Условия остановки и отката

- Не прошла миграция, пропали старые аккаунты/доступ, gateway не healthy, ошибка auth/callback или нет ожидаемых модулей — остановить расширение выпуска. Не исправлять показатели добавлением фиктивных событий.
- При откате приложений оставить добавленные таблицы; они обратно совместимы. Не откатывать всю БД поверх новых регистраций и оплат. Восстановление backup — отдельная процедура с учётом записей после snapshot.
- Сбой callback оставляет pending outbox; не удалять его. Проверить HTTPS-домен, tenant slug, согласованность credentials и обновлённый gateway без печати значений ключей. Не перенаправлять события рабочего tenant в случайный тестовый gateway.
- Пока реальные операции не разрешены и не проверены, результат формулируется как «схема, локальные тесты и защищённые маршруты проверены», а не «подтверждена работа с WB, доставкой писем и оплатами».
