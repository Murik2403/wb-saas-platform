# Задачи для Codex по итогам аудита 2026-10-06/07

Аудит делался на чистой копии `codex/cabinet-ux-20260930` (254c898) и на боевом сервере (только чтение). Ссылки вида `файл:строка` относятся к этому коммиту. Деплой, как раньше, только из закоммиченного HEAD.

**Что уже изменено на проде вручную (не в репозитории):**
- Тариф сервера: 8 vCPU, 16 ГБ RAM, диск 160 ГБ (корневой раздел пока 49 ГБ, не расширен). Адрес прежний.
- Лимиты памяти выставлены командой `docker update`: gateway 1 ГБ, provisioner 256 МБ, Traefik 256 МБ, glitchtip-postgres 512 МБ. Они слетят при пересоздании контейнера: закрепить в `docker-compose.yml` или в скриптах выкладки.
- Создана папка `/opt/wb-saas/backups/marketing` (`root:10001`, `2770`), без неё у тенанта marketing не было ни одного бэкапа с 01.10.
- Удалены старые бэкапы mvvorokov/demo, 49 контейнеров отката, 90 неиспользуемых образов, 130 архивов `marketshelper_manual_*.zip` внутри тома mvvorokov (том 2,8 ГБ → 0,56 ГБ).
- Provisioner пересоздан с сетевым псевдонимом `provisioner` (раньше пропадал после ручной замены, gateway не мог достучаться). В `deploy_verified_provisioner.py` ты уже добавил псевдоним: проверь, что то же есть во всех путях пересоздания.

---

## P0: на проде сейчас

### 1. Токен Telegram-бота в логах и в Sentry
- `gateway/telegram_bot.py:39,48-50,61-63,71-73` (URL вида `.../bot<TOKEN>/method`), логирование в `:87,133,152,157,181`, `gateway/internal_routes.py:169,228`.
- `raise_for_status()` и сетевые исключения содержат URL целиком. Живые логи gateway за сутки: 58 строк с `/bot<id>:<token>/getUpdates`, 39 циклов `Telegram polling loop error`. Причина ошибок: `api.telegram.org` с сервера недоступен (ConnectTimeout), поэтому бот поддержки не работает.
- Сделать: ловить `requests.RequestException` внутри `_call`/`send_document`/`send_message` и пробрасывать исключение без URL (`from None`); в `gateway/sentry_integration.py` и `tenant-app/sentry_integration.py` добавить `before_send`, вырезающий `bot\d+:[\w-]+`, `Authorization`, `Api-Key`, `Client-Id` на любой глубине, `include_local_variables=False`; не логировать цикл polling при каждой сетевой ошибке (backoff).
- Владелец перевыпустит токен бота после выкладки.

### 2. `/provisioning` отдаёт 500 в момент, когда кабинет стал `running`
- `gateway/app.py:940`: `accounts.tenant_has_access(conn, tenant)` вызывается после выхода из `with control_db.connect()`, соединение закрыто → `sqlite3.ProgrammingError: Cannot operate on a closed database`. Страница обновляется каждые 3 с, каждый новый пользователь в момент успеха увидит 500 вместо перехода в кабинет. Воспроизведено локально. Соседний `_redirect_for_account` написан правильно.
- Сделать: перенести вызов внутрь `with`, добавить тест на ветку `running`.

### 3. Бэкапы
- **Папка нового тенанта.** `provisioner/app.py:~364` `_backup_volume`: `backup_dir.mkdir()` под uid 10001, родитель `/opt/wb-saas/backups` — `root:root 755` → `PermissionError`. Затем вспомогательный контейнер `tar` для не-demo запускается под root с `cap_drop=ALL` (нет `CAP_DAC_OVERRIDE`) и не может писать в папку `10001:10001`. Исправить: запускать вспомогательный контейнер под `user: "10001:10001"` для всех тенантов; права на каталог (`root:10001 2770`) выдавать при `create_tenant` или в init-скрипте хоста; тест «бэкап нового тенанта при пустом BACKUP_DIR».
- **Открытый `.partial` в `BACKUP_DIR`** (`provisioner/app.py:368-392`). При сбое между `tar` и `encrypt_file` открытый архив остаётся навсегда, `_prune_old_backups` ищет только `*.tar.gz.enc`, `rclone sync` может унести его в S3. Шифровать потоком (как `export_tenant`) либо писать tar вне `BACKUP_DIR`, чистить `.*.partial` при старте.
- **Файлы без `.enc`** (`mvvorokov-*.tar.gz` с августа, `0644`) не удалялись ротацией и лежали открытыми: ротация должна учитывать оба формата.
- **Скрипт выкладки раздувает том.** `scripts/deploy_verified_tenant.py`: `create_backup(keep=1000000)` внутри `docker exec` под root → по zip-архиву (15–25 МБ) на каждую выкладку, нигде не удаляется (старые принадлежат root, а чистка идёт под 10001 и молча глотает `OSError`). У mvvorokov накопилось 144 файла, 2,5 ГБ, и они попадали в ночной бэкап тома (2,3 ГБ). Исправить: запускать `docker exec -u 10001`, `keep` разумный (например 5), не глотать ошибки удаления молча.
- **Таймаут клиента.** `scripts/backup_tenant_volumes.py` ждёт ответ 120 с, а архив 2 ГБ пишется дольше: cron пишет `Read timed out`, при этом provisioner дописывает файл в фоне и результат нигде не фиксируется. Нужен асинхронный запуск и опрос статуса, либо таймаут побольше и явный итог в лог.
- **Алерт.** Ошибки бэкапа никому не приходят (marketing 6 ночей подряд, mvvorokov 4). Добавить письмо или Telegram при неуспехе и проверку свежести (нет `.enc` младше 26 ч → тревога).
- **Офсайт.** `/etc/cron.d/wb-saas-offsite-backup`: `rclone sync` переносит локальные удаления и порчу в S3 в ту же ночь. Заменить на `copy` (или включить версионирование бакета), включить `-v --log-file`, добавить контроль свежести офсайта (лог пуст с 21.08). Ключ шифрования бэкапов лежит на том же хосте, что и сами архивы: для офсайта нужен отдельный.
- **Ротация логов Docker.** У gateway и тенантов `json-file` без `max-size` (в `daemon.json` только `ip6tables`).

### 4. Безопасность: роль по умолчанию и права менеджера
- `mh-redesign-lab/tenant-api/api_server.py:931` (и `:997`, `:3801`, `:4073`, `shop_context.py:34`, `shop_workspaces_api.py:59/73/90`, `production_workspace_api.py:301/324`, `manufacturing_costs_api.py:119`): `headers.get("x-tenant-role", "owner")`. Без заголовка запрос считается запросом владельца: любой вход в обход ForwardAuth даёт полные права на запись. Сейчас закрыто только тем, что Traefik всегда ставит заголовок. Исправить: fail-closed (нет заголовка → 401/403), общий секрет `X-MH-Gateway-Auth` от gateway (через `authResponseHeaders`), который tenant сверяет `hmac.compare_digest`; в nginx тенанта явно перезаписывать `X-Tenant-*`, не пробрасывать `$http_*`.
- Demo без ForwardAuth (`provisioner/app.py:612-621`): принудительно роль `viewer` и вычищать входящие `X-Tenant-*`.
- **Telegram-канал владельца может перехватить менеджер.** `POST /operations/reports/telegram/link-code` (`api_server.py:2560`) без проверки роли; код погашается как `UPDATE tenant_instances SET telegram_chat_id=? WHERE account_id=?` (`gateway/logic/accounts.py:684`), то есть на все кабинеты владельца. Разрешить только owner и привязывать код к аккаунту вызывающего.
- Менеджер = owner для всех записей (`production_permissions.py:57-58`). Решение владельца: какие действия сделать owner-only. Кандидаты: `settings/wb-token`, `settings/ozon` (POST/DELETE), `products/prices/upload`, `PATCH products/cards/*`, `operations/backups`, `settings/tax*`, импорт и удаление данных, `link-code`.
- Мелочи: гейты сравнивают путь по `request.url.path`, а роутер матчит `scope["path"]` (расхождение при `%3F`/`%23`): использовать `scope["path"]` и отклонять закодированные `?`, `#`, `..`.

---

## P1: до следующего релиза

### 5. Корректность и CI
- `POST /api/v1/dashboard/products/price-advice` читает данные, есть в `READ_ONLY_POST_PATHS`, но middleware (`api_server.py:934-943`) блокирует его для viewer и для демо: панель цен в публичном демо всегда в ошибке. Добавить путь в `safe_supply_preview`.
- CI не запускает vitest (483 теста): `.github/workflows/ci.yml`, job `test-frontends`. Добавить. CI срабатывает только на push в main/master/dev и PR в main/master: пуши в `codex/*` без PR не проверяются.
- `scripts/tests` не в CI, 2 теста падают детерминированно (`test_unchanged_committed_inputs_make_exact_hash_manifest`, `test_deleted_module_refuses_overlay_leftovers`: «Changed runtime build contract»); гейт построен на `assert` и выключается при `python -O`.
- `mh-redesign-lab/.dockerignore.tenant-staging` Docker не читает (флага `--ignorefile` у `docker build` на сервере нет). Переименовать в `Dockerfile.tenant-staging.dockerignore` рядом с Dockerfile; сейчас приходится копировать вручную при сборке.
- Версии Node: CI 24, `Dockerfile.tenant-staging` `node:22-alpine`.
- `site_drift` каждый час шлёт CRITICAL `/robots.txt: Disallow added: ['/feedback']` (baseline от 29.09): обновить baseline или убрать запрет, если он не задуман.
- Каждый `docker compose exec` из cron печатает `WB_SAAS_POSTGRES_PASSWORD is not set`: если какой-то сервис compose использует переменную, пересоздание из этого контекста получит пустой пароль.

### 6. Мобильная вёрстка (кабинет сейчас сломан на телефоне)
Подробности и 153 скриншота у владельца, пути `marketshelper-audit-shots`.
- **БЛОКЕР.** `.content-grid.compact-flow` (`styles.css:1099`, `minmax(0,1.35fr) minmax(20rem,.65fr)`) перебивает мобильное правило `.content-grid{grid-template-columns:1fr}` (`:3583`): на 360–390 px «Производство → Смена» и «Поставки → Заявки и приёмка» сжимаются до 3 px и перекрываются. Добавить в медиазапрос ≤1120px `.content-grid.compact-flow{grid-template-columns:1fr}`.
- **ВЫСОКАЯ.** Все 26 проверенных диалогов (`.plan-drawer.operation-dialog`) обрезаны справа: `.plan-drawer{width:min(100vw,29rem)}` (`:3645`) перебивает `width:min(32rem,100%)`. Задать `.plan-drawer.operation-dialog{width:min(32rem,100%)}` в медиазапросе.
- **ВЫСОКАЯ для обёртки.** `.shop-context-bar` не учитывает `env(safe-area-inset-top)`; отступ `--mobile-status-bar` применён ниже, к `.workspace`.
- **СРЕДНЯЯ.** Подписи нижней навигации 8–9 px, сетка на 5 колонок при 4 пунктах (`:3611`); после Tab фокус 7 раз уходит под нижнюю навигацию (`scroll-padding-bottom` нужен на `html`); фон под модальными окнами не блокируется; контраст 1,85:1 у `.signal.done.detail-trigger` и `.funnel-bar` в тёмной теме и 1,6:1 у активной вкладки `#tax-tab-calculation`; цели касания меньше 24 px (`details.shop-context-help > summary`, поля в «Счета и кассовые окна»); панель магазина + шапка занимают 46% высоты экрана.
- **НИЗКАЯ.** Мелкий текст (9,9–11,5 px), сырые тексты ошибок API («/api/v1/settings: HTTP 404»), `orientation: portrait-primary`.
- Производительность: один JS-чанк 889 кБ, LCP 3,9 с на Slow 4G. `React.lazy` по страницам.

### 7. Память и масштаб (на 20–30 клиентов)
В контейнере тенанта четыре отдельных процесса Python, каждый с pandas: API (uvicorn) 612 МБ, `sync.py` 212 МБ, `agent_sync.py` 134 МБ, `report_scheduler.py` 127 МБ (`mh-redesign-lab/deploy/tenant-staging-entrypoint.sh`). У mvvorokov за 6 дней 10 срабатываний OOM (лимит 1,5 ГБ).
- Объединить три фоновых процесса в один (или грузить pandas лениво): экономия 300–400 МБ на тенант.
- Ограничить `dashboard_cache` по объёму, а не только по числу записей (128).
- Автоостановка кабинетов без входа несколько дней (особенно триал) и запуск при входе.
- Лимиты по классам клиентов (триал 512 МБ, платный 1,5–3 ГБ); общая сумма лимитов не должна превышать RAM.
- Wildcard-сертификат `*.app.marketshelper.ru` через DNS-01: сейчас отдельный сертификат Let's Encrypt на тенант (лимит 50 в неделю).
- `get_management_economics` (`api_server.py:~3127`) пересобирает весь дашборд для каждого расхода, кэш на одну запись вытесняется.

### 8. Инфраструктура и защита в глубину
- **В репозитории** `docker-compose.yml:35-37` подключает `docker-socket-proxy` к `wbsaas_net` (где gateway и Traefik) при `POST=1`, `CONTAINERS_CREATE=1`: при взломе gateway это root на хосте. На проде прокси сейчас только в `provisioning_net`, то есть репозиторий расходится с продом: привести в соответствие, для Traefik отдельный read-only прокси (`POST=0`).
- `mh-redesign-lab/deploy/docker-compose.redesign-tenant.yml:6-7` подключает тенант к `wbsaas_net`, без `cap_drop`/`read_only`/`user`/ключа шифрования; комментарий в файле говорит обратное.
- Ключ шифрования: `WB_SAAS_TENANT_ENCRYPTION_KEYRING` (`gateway/provisioning.py:31`, `config.py:163`) отправляется в каждый тенант одним общим JSON; если в нём окажутся ключи других тенантов, скомпрометированный тенант расшифрует чужие бэкапы. Выводить keyring для slug отдельно.
- Provisioner без собственной аутентификации: убрать Traefik из `provisioning_net`, добавить общий bearer-токен между gateway и provisioner; `verify_internal_secret` (`provisioner/app.py:843`) сравнивать через `hmac.compare_digest`.
- Заголовки кабинета тенанта: нет `X-Frame-Options`/`frame-ancestors`, HSTS, `X-Content-Type-Options` (`deploy/nginx.tenant-staging.conf`); `Cache-Control: no-store` на `/api/`.
- Gateway не ограничивает размер тела запроса: `/internal/send-report-email` разбирает multipart целиком до проверки секрета (`internal_routes.py:104-115`, `:193`). Traefik `buffering.maxRequestBodyBytes` или ASGI-middleware.
- Блокировка по аккаунту при переборе пароля (сейчас только IP, 10/мин); срок сессии 30 дней (для мобильной версии нужны устройство-специфичные токены с отзывом); `/logout` без CSRF.
- CORS-whitelist `localhost:3000/3001` в проде, старые токены WB без `enc:v1:` читаются как открытый текст, если ключа нет (`tenant-app/secure_storage.py:39-40`): отказываться стартовать без ключа.
- CSV: везде набор префиксов `= + - @ \t \r` (`finance_csv.py:14`, `db/historical_attachments.py:90`, `TaxReconciliationPanel.tsx:55`; эталон `src/csv.ts:4`); старый `tenant-app/control_service.py:284` и Streamlit без защиты. nginx тенанта без `client_max_body_size` (по умолчанию 1 МБ), а импорт расходов обещает 5 МиБ.
- `.gitignore:51` игнорирует только `.env`: добавить `.env.*`, `!.env*.example`, `*.pem`, `*.key`.
- Мёртвый код и мусор в корне: Streamlit-интерфейс (`tenant-app/pages/*`, `app.py`, около 8700 строк), `frontend/` (Next.js, собирается в CI, нигде не деплоится), `vite.contour.config.ts`/`src/contour/`, `apex-motors.html`, `del-mar-*.html`, `task-*.md`, `VIDEO_TUTORIAL*.md`, `obs_record_control.py`.

---

## P2: мобильные приложения (для сторов), код и тексты

(Юридические действия владельца вынесены отдельно: уведомление в РКН, отдельное согласие на ПДн, аккаунты разработчика, модель оплаты, решение по правам менеджера.)

- Режим «магазинной сборки»: скрыть «Подписка и оплата», цены и ссылки на `/billing`, `/pricing`, блокировать навигацию на ЮKassa (Apple 3.1.1, Google Play Billing).
- Архитектура входа: у каждого клиента свой origin `{slug}.app.marketshelper.ru`, вход на `marketshelper.ru`; TWA не подходит (один APK не знает новых origin). Рекомендуется Capacitor с `server.url` на gateway и `allowNavigation: ["marketshelper.ru","*.app.marketshelper.ru"]`.
- Публично, без ForwardAuth и редиректа, отдавать `/.well-known/assetlinks.json` и `apple-app-site-association` (`provisioner/app.py:199-213` — публичный PWA-роутер только для четырёх путей).
- Скачивание через `blob` + `a.download` в ~10 местах (`data-source.ts:894,1287`, `AdsAnalyticsPanel.tsx:63`, `FbsOrdersPanel.tsx:74` и другие) не работает в WKWebView: единый хелпер «blob → Filesystem → Share sheet».
- Нативный push (APNs/FCM/RuStore Push), Universal Links и App Links для `/verify-email`, `/reset-password`, `/team/accept`.
- Удаление аккаунта: переименовать пункт меню «Пароль и email» в «Аккаунт и удаление», публичная страница «Как удалить аккаунт» (Google Data safety), проверить отмену рекуррента ЮKassa при удалении, не терять платёжные записи, которые политика обещает хранить (`gateway/logic/db.py:214,230` — `ON DELETE CASCADE`).
- Постоянный подтверждённый аккаунт для ревьюеров сторов на демо-данных.
- Иконки: отдельная maskable 512 с отступом ≥10%, 1024 без альфы для iOS, адаптивная Android, `id` и `screenshots` в манифесте, `apple-touch-icon` 180; `sw.js`: кэшировать только `response.ok && type==="basic"`, чистить старые ассеты, баннер «доступно обновление».
- Тексты: политика (п.7.4 говорит, что Метрики нет в кабинете, но `src/metrika.ts` её подключает после согласия; Ozon и Telegram-поддержка не названы; разделы про мобильные приложения), оферта (противоречие «отмена продления» / «доступ закрывается немедленно» / «возврату не подлежит»), `compliance_status.json` (срок уведомления РКН об инциденте 24 часа, не 72; публичная страница показывает внутренний чек-лист).
- Чеки 54-ФЗ: `gateway/config.py:68` `RECEIPT_ENABLED` по умолчанию `0`: проверить боевой `.env`.

---

## Что в порядке (не трогать)
Изоляция тенантов и сессии (HttpOnly, SameSite=Lax, sha256 в БД, новая сессия при входе, отзыв при смене пароля), CSRF gateway и Origin-проверка tenant API, шифрование токенов Fernet с ключом тенанта, шифрование бэкапов AES-256-GCM, отсутствие SQL-инъекций/SSRF/XSS, зависимости без уязвимостей (`npm audit`, `pip-audit`), мультимагазинный диспетчер (fail-closed), все новые маршруты в `shop_route_scopes.json`, TLS (минимум 42 дня), порты наружу только 22/80/443, PWA не кэширует приватные данные. Тесты: 2 542 прошли, 0 нестабильных.
