# Развёртывание WB SaaS: шлюз + отдельный контейнер на клиента

Ничего из этого не проверялось живьём в песочнице, где это писалось — там
нет доступа ни к Docker Hub (скачать `python:3.12-slim`, `traefik:v3.0`),
ни к PyPI (поставить `fastapi`/`docker-py`). Зато `docker-compose.yml` и
`traefik/traefik.yml` прогнаны через `docker compose config` и реальный
YAML-парсер — синтаксически и структурно они корректны. Первый настоящий
прогон (регистрация → провижининг → вход на поддомен) нужно сделать на
сервере из этого чек-листа, прежде чем пускать первого клиента.

## Что нужно заранее

- VPS с публичным IP, Docker и Docker Compose (`docker compose version`
  ≥ v2).
- Домен. Понадобится:
  - `wbsaas.example.com` (или ваш домен) → A-запись на IP сервера — это шлюз
    (вход/регистрация).
  - `*.app.wbsaas.example.com` → wildcard A-запись на тот же IP — это
    поддомены клиентов (`{slug}.app.wbsaas.example.com`).
- Порты 80 и 443 открыты наружу (нужны Traefik для HTTP-01 challenge
  Let's Encrypt и самого трафика).

## Обновление зависимостей

Python-зависимости приложений закреплены в `requirements.lock`: в образы и
CI устанавливаются только эти файлы с проверкой SHA-256. Не редактируйте
lock-файлы вручную. Для планового обновления на Python 3.12 обновите
соответствующий `requirements.in`, затем из корня репозитория выполните:

```powershell
python -m pip install "pip-tools==7.6.1"
python -m piptools compile --resolver=backtracking --generate-hashes --allow-unsafe --strip-extras --output-file gateway/requirements.lock gateway/requirements.in
python -m piptools compile --resolver=backtracking --generate-hashes --allow-unsafe --strip-extras --output-file provisioner/requirements.lock provisioner/requirements.in
python -m piptools compile --resolver=backtracking --generate-hashes --allow-unsafe --strip-extras --output-file tenant-app/requirements.lock tenant-app/requirements.in
```

Перед слиянием обновления выполните `pip-audit -r <путь к requirements.lock>`
для каждого Python-компонента, `npm audit --audit-level=high` в обоих
frontend-каталогах, тесты и сборку всех трёх образов. Dependabot создаёт
такие предложения еженедельно; проверяйте их вместе с результатами CI.

## Шифрование токенов и бэкапов

До запуска задайте в `.env` два независимых случайных секрета длиной не
менее 32 символов: `WB_SAAS_TENANT_ENCRYPTION_MASTER_KEY` и
`WB_SAAS_BACKUP_ENCRYPTION_KEY`. Первый остаётся в gateway: для каждого
клиента из него выводится отдельный ключ, а в Docker-volume записывается
только шифротекст WB/Ozon-учётных данных. Второй известен только
provisioner и шифрует каждый host-бэкап в формате `.tar.gz.enc`.

Не меняйте эти значения без миграции: старые токены и архивы после этого
нельзя будет расшифровать. При первом запуске с ключом старый plaintext
fallback-токен автоматически переписывается в шифротекст. До настройки
ключа новый tenant не сохранит токен, а host-бэкап завершится ошибкой,
вместо создания открытого архива.

Для аварийного восстановления сначала расшифруйте архив внутри provisioner
в временный файл, извлеките его в нужный volume отдельным одноразовым
контейнером, затем удалите временный `.tar.gz`:

```bash
docker compose exec -T provisioner python decrypt_backup.py /backups/<slug>/<archive>.tar.gz.enc /backups/<slug>/restore.tar.gz
docker run --rm -v wb-tenant-data-<slug>:/data -v /opt/wb-saas/backups:/backup:ro alpine:3.20 tar xzf /backup/<slug>/restore.tar.gz -C /data
rm /opt/wb-saas/backups/<slug>/restore.tar.gz
```

## Доверенные reverse proxy

По умолчанию Traefik принимает публичный трафик напрямую, поэтому оставьте
`WB_SAAS_TRUSTED_PROXY_HOPS=0`. Не включайте `forwardedHeaders.insecure`:
подставленный клиентом `X-Forwarded-For` мог бы обойти rate-limit. Если перед
Traefik ставится CDN или load balancer, сначала добавьте его фиксированные
CIDR в `traefik/traefik.yml` → `forwardedHeaders.trustedIPs`, затем задайте
в `.env` число таких proxy в `WB_SAAS_TRUSTED_PROXY_HOPS` и проверьте вход
с поддельным `X-Forwarded-For`.

## Мониторинг и отчёты об ошибках

Sentry включается только после явного задания DSN — без переменных ниже
приложение не отправляет телеметрию. Создайте два проекта в Sentry: один для
gateway, второй для tenant-приложений. Их DSN задаются в `.env` только на
сервере:

```dotenv
WB_SAAS_SENTRY_DSN=https://...  # проект gateway
WB_SAAS_SENTRY_TENANT_DSN=https://...  # общий проект tenant-приложений
WB_SAAS_SENTRY_ENVIRONMENT=production
```

В SDK отключена отправка персональных данных по умолчанию; не добавляйте в
Sentry tags, breadcrumbs или сообщения токены WB/Ozon, email, номера заказов
и прочие данные продавцов. После изменения этих переменных пересоберите
gateway, затем образ tenant и обновите уже запущенные tenant-контейнеры:

```bash
docker compose up -d --build gateway
docker build -t wb-dashboard-tenant:latest ./tenant-app
docker compose exec -e PYTHONPATH=/app gateway python3 /app/scripts/upgrade_all_tenants.py
```

Для внешней доступности создайте в UptimeRobot HTTP(s)-monitor с адресом
`https://<ваш-домен>/healthz`, интервалом 5 минут и уведомлением на рабочий
email/канал. Эндпоинт возвращает `{"status":"ok"}` без авторизации; не
используйте для мониторинга страницу входа или tenant-поддомен.

Отдельно контролируйте хотя бы один работающий tenant: создайте второй
HTTP(s)-monitor с адресом
`https://<slug>.<tenant-prefix>.<parent-domain>/_stcore/health` и тем же
интервалом. Provisioner открывает только точный `GET` к этому liveness-path;
сам dashboard и остальные запросы к tenant-поддомену по-прежнему защищены
ForwardAuth. Выберите живой tenant, а не вымышленный или остановленный demo.

## Первый запуск

```bash
cp .env.example .env
# отредактируйте .env: WB_SAAS_PARENT_DOMAIN, WB_SAAS_ACME_EMAIL как минимум

# образ, из которого будет подниматься КАЖДЫЙ новый клиент:
docker build -t wb-dashboard-tenant:latest ./tenant-app

docker compose up -d --build
docker compose logs -f gateway   # смотрите, что стартовало без ошибок
```

Начиная с версии с `provisioner`, socket Docker есть только у отдельного
внутреннего сервиса `provisioner`; `gateway` получает лишь узкий API для
жизненного цикла тенанта. Не публикуйте порт provisioner и не возвращайте
`/var/run/docker.sock` в gateway ради отладки. При обновлении уже
существующей установки один раз перед перезапуском дайте данным gateway
владельца непривилегированного процесса:

```bash
docker compose run --rm --user 0 --entrypoint sh gateway -c 'chown -R 10001:10001 /app/data'
```

Для каждого уже созданного tenant-volume выполните ту же миграцию в
плановое окно через временный одноразовый контейнер; новые тома получают
владельца из образа автоматически. После этого запускайте обычный
`docker compose up -d --build`.

Откройте `https://wbsaas.example.com` — должна открыться страница входа.
Зарегистрируйте тестовый аккаунт, посмотрите, что:
1. Появляется страница «Готовим ваш личный кабинет» и через 10–60 секунд
   сама перекидывает на `https://<slug>.app.wbsaas.example.com`.
2. Контейнер реально поднялся: `docker ps --filter name=wb-tenant-`.
3. Открыть тот же поддомен в приватном окне без входа — должно кидать на
   `/login`, а не показывать чужой дашборд.
4. Выйти (`/logout`), зайти вторым тестовым аккаунтом — убедиться, что его
   поддомен не показывает данные первого (они физически в разных
   контейнерах/томах, но стоит проверить руками один раз).
5. В боковой панели дашборда должна быть кнопка «💳 Подписка», ведущая на
   `/billing` шлюза (это `WB_SAAS_BILLING_URL`, переданный в контейнер
   автоматически — если кнопки нет, контейнер поднят без пересборки после
   обновления образа, пересоберите `wb-dashboard-tenant`).

Если что-то не поднялось — `docker compose logs traefik` (проблемы с DNS/
TLS) и `docker compose logs gateway` (проблемы с provisioning.py — там
логируется полный traceback Docker-ошибки).

## Оплата (ЮKassa)

Модель: бесплатный пробный период (`WB_SAAS_TRIAL_DAYS`, по умолчанию 14
дней) сразу после регистрации, затем платная подписка
(`WB_SAAS_SUBSCRIPTION_PRICE_RUB` за `WB_SAAS_BILLING_PERIOD_DAYS`). Если
списание не удалось (истёк пробный период без привязанной карты, или карта
отклонена при продлении) — аккаунт переходит в `past_due` и сохраняет
доступ ещё `WB_SAAS_GRACE_PERIOD_DAYS` дней, затем `cancel`-ится и его
контейнер останавливается.

Бизнес-логика (`gateway/logic/billing.py`) полностью покрыта unit-тестами
(`gateway/tests/test_billing.py`) и прогоняется в песочнице без сети. Сам
HTTP-клиент к ЮKassa (`gateway/yookassa_client.py`) и вебхук
(`gateway/billing_routes.py`) — нет, там нужен реальный прогон на сервере с
доступом к api.yookassa.ru.

Клиент видит статус подписки и может сам её отменить на `/billing`
(«Отменить подписку» → страница-подтверждение `/billing/cancel` → доступ
закрывается немедленно, контейнер останавливается фоном) — данные при этом
не удаляются. Ссылка на `/billing` также встроена прямо в навигацию
дашборда (боковая панель, кнопка «💳 Подписка») — `provisioning.py`
передаёт её адрес в контейнер клиента через `WB_SAAS_BILLING_URL`
автоматически, вручную ничего указывать не нужно.

### 1. Получить shop id и секретный ключ

1. Зарегистрируйтесь как продавец на https://yookassa.ru/my (нужно
   российское юрлицо/ИП).
2. В настройках магазина включите приём платежей банковской картой.
3. Возьмите `shopId` и «Секретный ключ» (не «ключ для тестового режима»,
   если хотите принимать реальные деньги) — они идут в `.env`:
   ```
   WB_SAAS_YOOKASSA_SHOP_ID=...
   WB_SAAS_YOOKASSA_SECRET_KEY=...
   ```
4. Перед боевым запуском стоит один раз прогнать полный цикл (регистрация →
   `/billing` → оплата → вебхук → `current_period_end` сдвинулся) в
   тестовом режиме ЮKassa (тестовый shopId/ключ выдаются в том же личном
   кабинете) — это единственный способ проверить интеграцию, которую нельзя
   было проверить в песочнице, где это писалось.

Перед включением уведомлений запросите у менеджера ЮKassa актуальные CIDR
адресов отправителей и задайте их через запятую в
`WB_SAAS_YOOKASSA_WEBHOOK_CIDRS`. Пустое значение намеренно блокирует
`/webhooks/yookassa` (503): это не даёт произвольному посетителю расходовать
лимит исходящих проверок платежа.

### 2. Зарегистрировать вебхук

В личном кабинете ЮKassa → «Настройки» → «HTTP-уведомления» укажите:

```
https://wbsaas.example.com/webhooks/yookassa
```

(замените на ваш `WB_SAAS_PARENT_DOMAIN`) и включите события
`payment.succeeded` и `payment.canceled`. Важно: уведомления от ЮKassa не
подписаны — `yookassa_webhook()` никогда не доверяет телу запроса напрямую,
а перезапрашивает статус платежа по его id через авторизованный GET и
действует только на основе этого ответа (см. docstring в
`yookassa_client.py`). Это защищает от поддельных POST на угаданный URL
вебхука.

### 3. Ежедневный биллинг-цикл (cron)

`scripts/run_billing_cycle.py` — точка входа, которую нужно запускать раз в
сутки: списывает оплату с аккаунтов, у которых закончился период (по
сохранённому `payment_method_id`), переводит просроченные без оплаты
пробные периоды в `past_due`, и отменяет (+ останавливает контейнер)
аккаунты, у которых истёк льготный период. Идемпотентен — повторный запуск
в тот же день не приведёт к двойному списанию (списание привязано к
`current_period_end`, а запись платежа — к id платежа в ЮKassa).

```cron
# /etc/cron.d/wb-saas-billing — раз в сутки в 03:00 по времени сервера
0 3 * * * root cd /opt/wb-saas && docker compose exec -T -e PYTHONPATH=/app gateway python3 /app/scripts/run_billing_cycle.py >> /var/log/wb-saas-billing.log 2>&1
```

Запускать нужно строго **внутри контейнера `gateway`**, не с хоста: реальная
база аккаунтов (`logic/db.py`) живёт в Docker-томе `gateway_data`,
примонтированном на `/app/data` только внутри этого контейнера. Запуск с
хоста тихо открыл бы пустой файл `gateway/data/gateway.sqlite3` из
git-чекаута и "успешно" ничего бы не сделал. `docker-compose.yml`
монтирует `./scripts` в контейнер как `/app/scripts:ro`; `PYTHONPATH=/app`
обязателен на exec — сборка образа плоская (`COPY . .` из `./gateway`), и
собственный `sys.path.insert` скрипта (рассчитанный на соседнюю папку
`gateway/`) в этом layout ничего не находит. Проверьте вручную первый
прогон (`docker compose exec -e PYTHONPATH=/app gateway python3
/app/scripts/run_billing_cycle.py`) и посмотрите лог перед тем как класть в
cron.

Ненулевой код выхода — только если сам вызов к ЮKassa не прошёл
(сеть/API-ошибка); обычный отказ карты — это ожидаемый путь в `past_due`,
не ошибка, и алерт по коду выхода на него срабатывать не должен.

## Восстановление пароля (SMTP)

«Забыли пароль» на `/forgot-password` работает без дополнительной настройки
(ссылка на сброс просто логируется, а не отправляется), но чтобы реально
доставлять письма клиентам, заполните в `.env`:

```
WB_SAAS_SMTP_HOST=smtp.yourprovider.ru
WB_SAAS_SMTP_PORT=587
WB_SAAS_SMTP_USER=...
WB_SAAS_SMTP_PASSWORD=...
WB_SAAS_SMTP_USE_STARTTLS=1
WB_SAAS_SMTP_FROM_EMAIL=no-reply@wbsaas.example.com
```

Подойдёt любой обычный SMTP-провайдер (Yandex 360, Mailgun, SendGrid,
Postmark, свой postfix и т.п.) — код не завязан на конкретный сервис,
`gateway/mailer.py` использует только stdlib `smtplib`. Логика запроса/
применения ссылки (`gateway/logic/password_reset.py`) полностью покрыта
unit-тестами и прогоняется в песочнице без сети; сама отправка через
`smtplib.SMTP(...)` — нет, проверьте её на реальном хосте с реальными SMTP-
данными (зарегистрируйтесь тестовым аккаунтом → `/forgot-password` →
письмо действительно пришло → ссылка меняет пароль → старые сессии
разлогинены).

Если `WB_SAAS_SMTP_HOST` не задан, `/forgot-password` продолжает отвечать
одинаковым сообщением («если такой email существует, мы отправили
ссылку») — так и должно быть, это защита от угадывания зарегистрированных
email — но письмо никуда не уйдёт, только попадёт в лог `gateway`
контейнера. Это ловушка для тех, кто забыл настроить SMTP: `/forgot-
password` не покажет ошибку, просто ничего не придёт клиенту.

## Email-доставка PDF-отчётов (внутренний API)

Страница «Отчёты» в тенант-приложении умеет по расписанию генерировать
PDF-отчёт и, опционально, отправлять его на email аккаунта — это отдельный
путь от `/forgot-password` выше: тенант-контейнер вызывает
`POST /internal/send-report-email` на самом gateway (используя тот же
`gateway/mailer.py`/SMTP), а не наоборот. Тенант-контейнер намеренно не в
одной docker-сети с gateway (изоляция тенантов друг от друга), поэтому
вызов идёт через публичный HTTPS-домен, а не по внутреннему docker-имени, и
аутентифицируется общим секретом, а не пользовательской сессией.

Задайте в `.env`:

```
WB_SAAS_INTERNAL_API_SECRET=<длинная случайная строка>
WB_SAAS_MAX_REPORT_UPLOAD_BYTES=20971520
```

Сгенерировать: `openssl rand -hex 32`. Пустое значение полностью отключает
приём `/internal/send-report-email` (403 на любой запрос) — значение
обязательно нужно задать, чтобы email-доставка отчётов вообще заработала;
до этого чекбокс «Также отправлять на email аккаунта» на странице
«Отчёты» просто не показывается (см. `tenant-app/config.py`'s
`INTERNAL_API_SECRET`/`INTERNAL_API_URL`/`TENANT_SLUG`, устанавливаемые
`provisioning.py` при создании контейнера тенанта — значит после смены
`.env` требуется не только `docker compose up -d --build` для gateway, но
и `upgrade_all_tenants.py`, чтобы каждый уже существующий тенант получил
свежие переменные окружения).

Реально работает только если ещё и SMTP настроен (см. раздел выше) — без
него `send_report_email` просто логирует письмо, как и `/forgot-password`.

## Telegram-доставка PDF-отчётов

Второй канал доставки для страницы «Отчёты», поверх того же внутреннего
API и того же `WB_SAAS_INTERNAL_API_SECRET`, что и email выше. Использует
уже существующего бота поддержки (`WB_SAAS_TELEGRAM_BOT_TOKEN`,
см. `gateway/telegram_bot.py`) — отдельный бот не нужен.

Так как Telegram chat_id сам по себе не несёт привязки к email/аккаунту,
нужен явный шаг привязки: пользователь нажимает «Получить код для
привязки» на странице «Отчёты» → gateway генерирует одноразовый код
(`logic/accounts.create_telegram_link_code`, живёт 15 минут,
`telegram_link_codes` в `gateway/data/gateway.sqlite3`) → пользователь
пишет боту `/link <код>` → `telegram_bot.py`'s `_handle_link_command`
проверяет код и сохраняет `chat_id` в `tenant_instances.telegram_chat_id`.
После этого чекбокс «Также отправлять в Telegram» в форме отчёта
становится доступен.

Ничего дополнительно настраивать в `.env` не нужно сверх уже описанного
выше (`WB_SAAS_INTERNAL_API_SECRET`, `WB_SAAS_TELEGRAM_BOT_TOKEN`) — если
оба заданы, канал работает.

Проверка на реальном хосте: открыть «Отчёты» → «Получить код для
привязки» → отправить боту `/link <код>` → бот должен ответить
подтверждением → чекбокс «Также отправлять в Telegram» должен стать
доступен → «Сгенерировать сейчас» с этим чекбоксом → PDF должен прийти в
тот же чат.

## Выкатка исправления кода всем клиентам

Каждый клиент — отдельный контейнер из одного и того же образа
`wb-dashboard-tenant`. Поправили баг в `tenant-app/` — пересоберите образ и
прогоните апгрейд по всем активным клиентам (данные не трогаются, они в
отдельном Docker-томе):

```bash
docker build -t wb-dashboard-tenant:latest ./tenant-app
docker compose exec -e PYTHONPATH=/app gateway python3 /app/scripts/upgrade_all_tenants.py
```

Как и `run_billing_cycle.py`/`backup_tenant_volumes.py` (см. выше), этот
скрипт тоже читает реальную базу аккаунтов через `logic/db.py` — она живёт
только в `gateway_data`, томе контейнера `gateway`. Запуск голым `python3
scripts/upgrade_all_tenants.py` с хоста откроет пустой файл из
git-чекаута, увидит ноль клиентов и напечатает «No running tenants to
upgrade», ничего не сделав — при этом выглядит как успешный запуск. Сама
пересборка образа (`docker build ...`) — обычная команда докера на хосте,
её это не касается.

Скрипт останавливает и пересоздаёт контейнеры клиентов по одному,
дожидаясь health-check перед следующим — если новый образ сломан, вы
узнаете об этом на первом же клиенте, а не на всех сразу.

## Бэкапы

Два независимых уровня:

1. **Внутри тома** — `tenant-app/backup_tools.py` уже делает ежедневный
   бэкап SQLite прямо в томе контейнера. Защищает от испорченной базы
   внутри приложения, но НЕ от потери самого сервера/тома.
2. **На уровне хоста** — `scripts/backup_tenant_volumes.py`: копирует
   содержимое Docker-тома каждого клиента (включая тех, кто уже отменил
   подписку — их данные не удаляются автоматически) **и control-plane
   `gateway_data`** (аккаунты, платежи, сессии) в `.tar.gz.enc` на диске
   хоста (`WB_SAAS_BACKUP_DIR`, по умолчанию `/opt/wb-saas/backups`).
   Provisioner сам находит только `gateway_data` текущего Compose-проекта
   по Docker-меткам: gateway не может передать ему произвольный mount.
   Архивы шифруются, хранятся в каталогах `<slug>/` и `control-plane/` и
   подчищаются старше `WB_SAAS_BACKUP_KEEP_DAYS` дней (по умолчанию 14).

```cron
# /etc/cron.d/wb-saas-backup — раз в сутки в 02:00, до биллинг-цикла в 03:00
0 2 * * * root cd /opt/wb-saas && docker compose exec -T -e PYTHONPATH=/app gateway python3 /app/scripts/backup_tenant_volumes.py >> /var/log/wb-saas-backup.log 2>&1
```

Тот же аргумент, что и для биллинг-цикла: скрипт тоже читает
`logic/db.py`, поэтому обязан выполняться внутри контейнера `gateway`, а
не с хоста — иначе он "успешно" отбэкапит ноль клиентов из пустой базы.
`WB_SAAS_BACKUP_DIR` (путь `/opt/wb-saas/backups` по умолчанию) при этом
всё равно означает путь на **хосте**. Gateway передаёт только имя уже
известного tenant-volume в закрытый provisioner; тот использует жёстко
заданный образ и путь бэкапа. Docker socket в gateway для этого больше не
нужен.

Восстановление control-plane относится к SQLite-установке: остановите
gateway, расшифруйте архив из `control-plane/`, извлеките его в тот же
`gateway_data` volume и только затем запустите gateway. Для Postgres нужен
отдельный согласованный `pg_dump`/PITR: snapshot `gateway_data` его не
содержит.

**Важно**: сам по себе `WB_SAAS_BACKUP_DIR` на том же сервере не защищает
от потери сервера целиком — это тот же самый диск. Настройте отдельно
синхронизацию этой папки во внешнее хранилище (`rclone`/`aws s3 sync`/
обычный `rsync` на другой сервер и т.п.) — способ намеренно не зашит в
скрипт, чтобы не привязывать репозиторий к конкретному провайдеру.
Требует образ `alpine` — на первом прогоне он подтянется из Docker Hub
автоматически (в отличие от песочницы, где это писалось, у реального VPS
есть доступ в интернет).

## Доступность и восстановление

Текущая топология — один VPS и один экземпляр gateway. Это **не HA**: отказ
VPS, Docker daemon, Traefik или gateway делает вход и ForwardAuth недоступными
до восстановления. При штатном рестарте gateway пользователи не должны
разлогиниваться: хэши токенов сессий лежат в control-plane БД, а CSRF-токен
хранится в cookie браузера. Сбрасываются только in-memory rate-limit и кэш
имени Telegram; незавершённая фоновая операция provisioning может потребовать
повторной проверки в админке.

Текущая эксплуатационная цель (не SLA до первого restore drill):

- **RPO:** до 24 часов после подтверждённого ежедневного шифрованного backup
  и его offsite-копии. Если offsite-синхронизация или cron не проверены, RPO
  не определён; архив на том же VPS не защищает от потери сервера.
- **RTO:** до 4 часов после готовности нового VPS и последней рабочей
  offsite-копии. Этот ориентир нужно измерить тестовым восстановлением до
  начала платной эксплуатации и затем повторять не реже раза в квартал.

Для перехода к HA потребуются одновременно Postgres с собственными
`pg_dump`/PITR и репликацией, минимум два gateway за Traefik, внешнее общее
состояние для rate-limit/фоновых задач (например Redis/очередь) и лидер для
периодических billing-задач. Не включайте вторую реплику поверх текущего
SQLite: одновременные writers не поддерживаются этой топологией.

## Переключение control-plane базы на Postgres

По умолчанию `gateway/logic/db.py` (аккаунты, сессии, биллинг, статусы
тенантов — НЕ данные самих тенантов, у каждого из них своя отдельная
SQLite внутри собственного контейнера) работает на одном файле SQLite.
Этого достаточно, пока запросы гейтвея не начинают реально упираться в
блокировки этого файла. Когда это станет проблемой:

1. Поднять Postgres: `docker compose --profile postgres up -d postgres`
   (сервис в `docker-compose.yml` — opt-in через `profiles: [postgres]`,
   так что обычный `docker compose up -d` его не трогает; без публичного
   порта, доступен только из `wbsaas_net`). Задать `WB_SAAS_POSTGRES_PASSWORD`
   в `.env` на сервере (без дефолта, в отличие от user/db — секрет).
2. Драйвер `psycopg[binary]` уже в `gateway/requirements.txt` — отдельно
   ставить не нужно, попадает в образ при обычной пересборке.
3. Задать `WB_SAAS_DB_BACKEND=postgres` и `WB_SAAS_DATABASE_URL=postgresql://wbsaas:<пароль>@postgres:5432/wbsaas`,
   пересоздать гейтвей (`docker compose up -d --build gateway`) — это создаст
   пустую схему на Postgres через `db.init_db()`.
4. Перенести накопленные данные: `docker compose exec -e PYTHONPATH=/app gateway
   python3 /app/scripts/migrate_sqlite_to_postgres.py` — переливает все 6
   таблиц из старого SQLite-файла построчно (интроспекция схемы, без
   хардкода списка таблиц/колонок), безопасно перезапускать (`ON CONFLICT
   DO NOTHING` по первичному ключу).

Весь остальной код (`logic/accounts.py`, `logic/billing.py`,
`logic/password_reset.py`) написан на переносимом SQL (`?`-плейсхолдеры,
доступ к строкам по имени колонки) и не требует изменений — переключение
полностью инкапсулировано в `db.py`. Юнит-тесты (`gateway/tests/`)
по умолчанию продолжают гонять SQLite независимо от `WB_SAAS_DB_BACKEND`;
`tests/test_db_backend_postgres.py` дополнительно поднимает настоящий
Postgres через testcontainers (нужен Docker на машине, где гоняются тесты —
иначе тест самоскипается) и гоняет `_PGConnection`/`_init_db_postgres`
против него напрямую.

## Ограничения текущей версии (сознательно отложено)

- Прямых unit-тестов нет у happy path'а Apply/Rollback ретроактивной FIFO-
  реконструкции (`db/wb_fifo_reconstruction_v67.py`) — сам бэкап/восстановление
  sqlite-файла целиком, в отличие от остальной денежной логики (83 теста
  в `tenant-app/`). Требует реального хоста, см.
  `tenant-app/README_REFACTOR.md` → «Шаг 3».
- Ограничение по ресурсам (`WB_SAAS_TENANT_MEM_LIMIT`/`_CPU_QUOTA` в `.env`)
  стоит подобрать под размер вашего VPS и число клиентов, которых
  собираетесь на нём разместить одновременно.
- Оплата (`gateway/yookassa_client.py`, `billing_routes.py`,
  `scripts/run_billing_cycle.py`) не прогонялась вживую — только
  статическим анализом и unit-тестами бизнес-логики вокруг неё (см.
  раздел «Оплата (ЮKassa)» выше). Обязательно пройти тестовый цикл оплаты
  перед первым платящим клиентом.
- Отправка email через `smtplib` (восстановление пароля) не прогонялась
  вживую — см. раздел «Восстановление пароля (SMTP)» выше.
- Хост-бэкап через `scripts/backup_tenant_volumes.py` (нужен образ `alpine`
  и настоящий Docker-демон) тоже не прогонялся вживую в этой сессии —
  проверьте на первом клиенте перед тем как полагаться на него.
