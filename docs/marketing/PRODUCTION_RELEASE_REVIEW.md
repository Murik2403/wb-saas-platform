# Проверка выпуска публичного gateway

Дата проверки: 19 сентября 2026. Сравниваемый кандидат: 959748e42bc5a837869adfc578cffde0fa4981ed; исходная база: 7a137a3d02e900b65b5e316b52b77f2a563ae1d5.

Статус: серверный доступ есть, публикация не выполнялась. Три расхождения публичных шаблонов, найденные относительно 959748e, устранены в окончательном коде `545c90b3f0a82c5c12adf2880b8153765b18a3a6`: ссылки сохранены, конфликт robots исправлен. Этот commit перенесён в основной проект; финальный gateway pytest — 264 passed, 76 subtests passed, 0 skipped. Эти пункты больше не являются блокерами. Перед переключением требуется согласованный backup и закрытая сверка действующих параметров control-plane. Отсутствие отдельного удалённого staging и старый Git HEAD сервера сами по себе не означают невозможность выпуска.

Дополнение после совместной работы с Claude Code: локально интегрирован `32329a27ea562818deaf119a7bc3b457cd675991` с четырьмя дополнительными marketing-маршрутами. Сверка сервера и таблицы хешей ниже относятся к предыдущему кандидату `545c90b` и его базе. Перед публикацией нового кандидата нужно повторить сравнение затронутых публичных файлов с текущим сервером; этот документ не подтверждает, что новый commit уже размещён. Результат интеграции и локальной проверки — в [CLAUDE_COLLABORATION.md](CLAUDE_COLLABORATION.md).

## Подтверждённая схема

| Параметр | Факт |
|---|---|
| SSH alias | marketshelper-staging |
| HostName / пользователь | mvvorokov.app.marketshelper.ru / root |
| Сервер | msk-1-vm-1vxh |
| Назначение | Действующий сервер: gateway маршрутизируется на marketshelper.ru; рядом работают tenant mvvorokov, demo и Traefik 80/443 |
| Compose project / service | wb-saas / gateway |
| Compose рабочий каталог / файл | /opt/wb-saas / /opt/wb-saas/docker-compose.yml |
| Серверный Git HEAD | 824d2a1b81d31f3b5834766e406cb4d8359c83d0 |
| Работающий контейнер | wb-saas-gateway-1, healthy |
| Работающий образ | sha256:0c0db951860a1854c57c4b4da73fa01391146d649ea397045b026513fc81efd4 |
| Тег / OCI revision | wb-saas-gateway:latest / revision отсутствует |
| Создание образа | 2026-09-17T21:29:02.836372461Z |
| База | Runtime config.DB_BACKEND = sqlite |
| Файл SQLite | /app/data/gateway.sqlite3, подтверждён db.DEFAULT_DB_PATH |
| Данные | Volume wb-saas_gateway_data → /app/data |
| Скрипты | Bind /opt/wb-saas/scripts → /app/scripts, read-only по Compose |
| Docker-сети gateway | wb-saas_provisioning_net и wbsaas_net |
| Параметры контейнера | user 10001:10001, readonly rootfs, restart unless-stopped |
| Docker Compose | v5.5.0; up поддерживает --no-deps, --no-build, --pull и --force-recreate |

Секретные значения, .env и ключевые файлы не читались и не выводились. Проверка config.DB_BACKEND вывела только имя backend. Сервер не изменялся; backup, миграции, сборка, отправка писем, платежи и перезапуск не запускались.

Git HEAD сервера не описывает работающий продукт полностью: на сервере накоплены изменения и неотслеживаемые исходники. Дополнительно проверены 48 обычных runtime-файлов и шаблонов внутри работающего контейнера: после нормализации CRLF → LF все совпали с серверным исходным деревом. Различия raw-хэшей 19 файлов оказались только окончаниями строк.

## Сравнение исходников с базой и кандидатом

Проверены все 117 путей из объединения Git-деревьев 7a137a3 и 959748e в gateway/ плюс scripts/run_billing_cycle.py. Для сервера использован git hash-object, содержимое секретных путей в выборку не включалось. Все 62 серверных tracked-пути этой области входят в выбранное объединение.

| Категория | Количество |
|---|---:|
| Сервер совпадает с baseline и target | 49 |
| Сервер совпадает только с baseline; target содержит ожидаемое обновление | 25 |
| Путь отсутствует на сервере | 33 |
| Сервер отличается от обоих commit | 10 |

Среди **38 modified tracked-файлов сервера**: 10 совпадают с обоими commit, 21 совпадает с baseline, 7 отличаются от обоих. Следовательно, сам большой dirty-список не является 38 конфликтами.

Среди **25 untracked-путей сервера**: 16 совпадают с обоими commit, 3 с baseline, 3 входят в отличающиеся от обоих пути, ещё 3 не входят в кандидат: gateway/billing.py и две копии gateway/templates/privacy.html.before-20260914-012235 / privacy.html.before-20260914-012457. Эти дополнительные файлы не следует автоматически включать в чистый Docker build. Их удаление не требуется и не предлагается.

### Отличающиеся файлы: точные Git blob hashes

| Путь | Сервер | Baseline 7a137a3 | Target 959748e |
|---|---|---|---|
| gateway/templates/compliance.html | 02a1dba6c91ee71360eea6be1a585247e1093311 | 0bb906ba65f0deee4aad48a18d3e99957b5e88ec | b38325ed3d88b41f62cf184e42ac6669969e1c80 |
| gateway/templates/contacts.html | b26dd1d1a991ce95cb5f0d553cdc5ae8557858c1 | 21c02d67412a9d00cf503505254e6f34c5852127 | 5ab9c916e2a804b6b896bf2c779e357246f2ab43 |
| gateway/templates/offer.html | 93c40ca51316423c242e3249bccfaaadeaeb895e | bec0a2d63c6e2b5dba8de1fa501c92f931646826 | a135d967387c09338a8fba5527edb600db05ef0e |
| gateway/tests/test_accounts.py | a63b7c102e017853eabdb81a95f371468a2aefa2 | 0d2367532d245fafd49f8c9946c5da25b5dda3aa | 0d2367532d245fafd49f8c9946c5da25b5dda3aa |
| gateway/tests/test_billing.py | 86e51ce1fb29f0cf1385aa8550e3ff513a68bef3 | 525b35f651bc410d6798716de2dbffd67001ed4f | 525b35f651bc410d6798716de2dbffd67001ed4f |
| gateway/tests/test_billing_routes_webhook.py | e10e70157edfa5f8718cb048481239c26cf0d875 | 9503aeac99c90271b6249e8c99a7ad52e99d5961 | 9503aeac99c90271b6249e8c99a7ad52e99d5961 |
| gateway/tests/test_internal_routes.py | db6e40a3d55be5e64e6508af664ac890164baccc | 2661c6c349c78b0ff26fc28f2ffe597172b3a997 | 2661c6c349c78b0ff26fc28f2ffe597172b3a997 |
| gateway/tests/test_password_policy_routes.py | c7eec60a9b2be5158f00c2d956140899e88db75f | 0741d067c22fea85157f56ec5a0bcc49cd6fd724 | 0741d067c22fea85157f56ec5a0bcc49cd6fd724 |
| gateway/tests/test_provisioning_credentials.py | 652c35ff5ab909594862f34d155eccdf90e2e82b | 8dde81bd232251d5933755ee700f59084c74b9e5 | 8dde81bd232251d5933755ee700f59084c74b9e5 |
| gateway/tests/test_rate_limiter.py | 5e5f053f2c940ec591279e054e16ddd48191f8e7 | c3ab6d33e4b629c30030cfd3283e26fe32b67f97 | c3ab6d33e4b629c30030cfd3283e26fe32b67f97 |

### Что означают отличия

**gateway/templates/contacts.html.** Сервер после абзаца о тарифах содержит:

~~~html
<p>Актуальное состояние обработки персональных данных и регламент реагирования на инциденты:
<a href="/compliance">здесь</a>.</p>
~~~

**gateway/templates/offer.html.** В п.7.2 сервер дополняет предложение о политике данных текстом:

~~~html
Дополнительно
обновлённый статус уведомлений в РКН, локализации и инцидентный план можно проверить на
странице <a href="/compliance">Комплаенс</a>.
~~~

В сравнивавшемся 959748e эти дополнения отсутствовали. Общий footer содержал только /privacy и /contacts. Координатор восстановил обе ссылки в последующих изменениях. Это расхождение закрыто; hash-таблица ниже сохраняет первоначальную точку сравнения и не описывает более поздний commit.

**gateway/templates/compliance.html.** На сервере нет старого block head с noindex,nofollow. В 959748e этот блок сохранялся вместе с общей index,follow; локальный preview выдавал две противоречащие robots-метки при включении страницы в sitemap. Координатор удалил конфликт и дополнил проверку всех robots-меток. Сообщённый результат после исправления: SEO: 14 тестов + 72 подслучая PASS. Этот пункт закрыт; действующая общая robots-метка согласована с публичной страницей.

**Остальные 7 файлов — тесты.** На сервере отсутствуют 20 test-функций, присутствующих в baseline/target; новых серверных test-функций нет. Это более старое покрытие: аккаунты 7, billing2, webhook1, internal routes3, password policy2, provisioning credentials2, rate limiter3. В internal routes также устарел mock-ответ Telegram. Эти тестовые отличия не требуют переноса в target и не блокируют публикацию сами по себе; tests/ исключён gateway/.dockerignore.

## Как выпускать именно gateway

Публичные страницы обслуживаются FastAPI из gateway/app.py, который подключает marketing router и общие шаблоны. Образ собирается из gateway/Dockerfile с контекстом gateway. Контейнер запускает uvicorn app:app на 8000. Поэтому копирование одних HTML не доставит новые маршруты и все зависимости.

В кандидат входят также consent/growth, админ-отчёты, lifecycle и аддитивные таблицы gateway. Выпуск gateway — это обновление приложения с миграцией control-plane, а не статическая загрузка страницы. Новый первый отчёт в существующих tenant при таком выпуске не появится: для него нужен отдельный последующий выпуск tenant. Изменять tenant image, provisioner, сети или backend ради публикации страниц не требуется.

**Скрипт на хосте обновляется отдельно от образа:**

| Файл | Сервер SHA-256 | Кандидат SHA-256 |
|---|---|---|
| scripts/run_billing_cycle.py | 94f4bc7105eff9af221951a008280ba5d97823dc56a380e808930d1829e7f83a | 95b2c9d5bdce1cf91fbe391e4a21060feedad04a042acd3d4a6e38e5f6fdd086 |

Его серверный Git blob точно равен baseline. Новый файл добавляет передачу признака тестового платежа; никаких специальных серверных правок в нём сохранять не требуется.

### Последовательность после завершения локальной проверки

1. Получить окончательный чистый commit, включающий уже выполненные исправления ссылок и robots; использовать его полный SHA, а не автоматически старый 959748e.
2. Повторить release/source gates и нужные тесты окончательного commit. Сборку вести из чистого экспортированного Git-дерева. Не выполнять git reset/clean/pull поверх грязного /opt/wb-saas и не собирать весь его gateway как новый release.
3. Передать на сервер отдельный пакет gateway + scripts/run_billing_cycle.py в отдельный каталог releases/<SHA>. Он не должен содержать .env, базы, ключи, серверные privacy.before-копии или старый gateway/billing.py.
4. Собрать новый образ с неизменяемым тегом и OCI revision. При необходимости проверить его на новом loopback-порту с отдельными пустыми данными, пустыми SMTP/платёжными/аналитическими настройками и без production-credential. Превью на ноутбуке уже есть; удалённого изолированного gateway-стенда сейчас нет.
5. Зафиксировать старый image ID отдельным rollback-тегом; сохранить host billing-script, deployment manifest и согласованный backup SQLite. Не менять текущие .env/секреты, parent-domain, DB-backend, volume или tenant-image.
6. Проверить в памяти, без печати значений, что эффективные старые и новые Compose-настройки для gateway отличаются только выбранным image. Старый серверный Compose не заменять локальным целиком. Он уже отличается от Git HEAD; конфигурация сети, БД и работающих tenants остаётся текущей.
7. Переключить только gateway через небольшой image-only override и --no-deps --no-build --pull never. После healthy атомарно заменить только host scripts/run_billing_cycle.py; выполнять это вне активного billing-cycle и не запускать сам цикл для проверки.
8. Проверить health, главную, pricing, все восемь новых публичных URL, robots/sitemap, единственность robots, canonical, consent и отсутствие счётчика до согласия. Проверить существующие auth/forward-auth пути без создания покупок или реальных регистраций. Сверить image ID, backend=sqlite и прежние mounts. Новые таблицы проверять только на существование, не публикуя строки.
9. Зафиксировать выпущенные SHA/image ID и факт проверки. Последующий выпуск tenant, настройка счётчика и реальные внешние E2E остаются отдельными этапами.

### Команды переключения, подготовленные для рецензии

Команды ниже **не выполнялись**. Имена сервиса, пути и наличие CLI-флагов проверены. RELEASE_SHA и RELEASE_DIR задаются после окончательного commit; данный файл не разрешает запуск до готового backup, финального commit и проверки неизменности runtime-настроек.

На сервере в отдельном каталоге источников, без чтения .env:

~~~sh
RELEASE_SHA=<полный_SHA_проверенного_commit>
RELEASE_DIR=/opt/wb-saas/releases/$RELEASE_SHA
docker build --label org.opencontainers.image.revision=$RELEASE_SHA --tag mh-gateway:$RELEASE_SHA "$RELEASE_DIR/gateway"
docker image tag sha256:0c0db951860a1854c57c4b4da73fa01391146d649ea397045b026513fc81efd4 mh-gateway:rollback-20260919
~~~

Подготовленный image-only override в RELEASE_DIR/gateway-image.yml:

~~~yaml
services:
  gateway:
    image: mh-gateway:<полный_SHA_проверенного_commit>
~~~

Использовать существующий Compose project и существующий первый конфигурационный файл:

~~~sh
cd /opt/wb-saas
docker compose --project-directory /opt/wb-saas -p wb-saas -f /opt/wb-saas/docker-compose.yml -f "$RELEASE_DIR/gateway-image.yml" up -d --no-deps --no-build --pull never gateway
docker inspect wb-saas-gateway-1 --format '{{.State.Health.Status}} {{.Image}}'
~~~

Compose при реальном переключении сам использует существующую серверную конфигурацию. В этом аудите compose config и чтение .env не выполнялись. До команды нужен закрытый preflight сравнения параметров без вывода секретных значений; сохранение файла .env без редактирования само по себе не доказывает, что он не менялся после предыдущего запуска.

После healthy и сохранения старой версии скрипта под защищённым RELEASE_DIR/rollback:

~~~sh
install -m 0644 "$RELEASE_DIR/scripts/run_billing_cycle.py" "/opt/wb-saas/scripts/.run_billing_cycle.$RELEASE_SHA"
mv "/opt/wb-saas/scripts/.run_billing_cycle.$RELEASE_SHA" /opt/wb-saas/scripts/run_billing_cycle.py
sha256sum /opt/wb-saas/scripts/run_billing_cycle.py
~~~

Последующий compose up без этого image override может вернуть иной образ. Поэтому точный manifest/команда должны стать частью записи выпуска, а не временным неучтённым действием.

## Backup и rollback: что уже существует и чего не доказано

На сервере существуют scripts/backup_tenant_volumes.py, scripts/restore_local.py, scripts/deploy_verified_tenant.py, /opt/wb-saas/backups, /etc/cron.d/wb-saas-backup и /etc/cron.d/wb-saas-billing. Наличие файлов не подтверждает успешность последней резервной копии.

backup_tenant_volumes.py совпадает с нашей исходной версией по SHA-256 b785412a0eadb7266aef7e420278c9575e08670f8b1c542f8762462ea12419c9 и содержит маршрут /v1/control-plane/backup. DEPLOY.md описывает шифрованные архивы control-plane/ и restore в тот же gateway_data volume.

Выполняемый provisioner/app.py и серверный исходник также совпали с нашим исходником по SHA-256 cbaf8fe5c83be085d6d5dc9d042c5ca279083fe7f5fc88434c2de528cc4386d7. Его _backup_volume запускает tar по live volume; там нет SQLite backup API или остановки писателей. Поэтому такой архив нельзя считать доказанным согласованным SQLite snapshot при параллельных записях.

Перед выпуском нужен один из проверяемых вариантов: SQLite connection.backup() в отдельный файл с integrity_check и проверкой восстановления; либо согласованная остановка всех писателей перед архивом. Резервную копию хранить в защищённом серверном backup-каталоге, по действующей процедуре шифрования, вне исходников и Git. Не копировать только открытый gateway.sqlite3, игнорируя WAL. В этой задаче backup и restore drill не исполнялись.

Сохранить:

- прежний immutable image ID и rollback-тег;
- старый scripts/run_billing_cycle.py, права и контрольную сумму;
- действующий Compose manifest/overrides и конфигурацию секретов в существующем защищённом месте, без вывода содержимого;
- согласованную копию control-plane и результат проверки её целостности;
- dirty-серверные исходники и служебные копии оставить на месте: новый образ не требует их перезаписи.

Откат приложения: отдельный image-only override на mh-gateway:rollback-20260919, та же команда up только для gateway, затем атомарное восстановление старого host billing-script. Проверить healthy, image, auth и публичные страницы. Не удалять gateway_data и не менять tenant-контейнеры.

Новые growth-таблицы аддитивны; при обычном откате приложения оставить их. Автоматический откат всей БД поверх новых регистраций и платежей недопустим. Восстановление базы — отдельная согласованная процедура при повреждении/неуспешной миграции с учётом записей после snapshot.

deploy_verified_tenant.py сохраняет предыдущий **tenant**-контейнер для отката; это не механизм выпуска gateway. restore_local.py восстанавливает tenant backup; не использовать его как готовый restore control-plane.

## Что устарело в прежнем отчёте

Это замечания к снимку документов на commit79bf3fd; координатор обновляет STAGE1_STATUS/runbook отдельно.

- STAGE1_STATUS: «незакоммиченная копия/dirty» устарело для проверенного чистого 79bf3fd. Во время текущей работы появились исправления CI; затем сформирован 959748e. Состояние нужно привязывать к SHA, а не к прежнему абзацу.
- Там же фраза «tenant redesign ещё не включён в CI» противоречит source: tenant-staging уже есть в matrix. При этом в 79bf3fd были неверные cwd/Dockerfile paths CI; позднее их исправили, поэтому наличие matrix не является доказательством зелёного CI.
- GitHub Actions для точного 79bf3fd на момент проверки вернул пустой список. Это не утверждение о более поздних commit.
- Число 439 = 254 gateway + 139 tenant API + 29 Vitest + 8 JS + 7 packaging — исторический запуск. Новые CI-contract тесты увеличивают состав; итог нужно брать из нового полного прогона. Это число не включает все репозиторные suites.
- DEPLOY.md всё ещё описывает старт пробы «сразу после регистрации»; source текущего release начинает её после успешной подготовки кабинета.
- В staging-deploy.md описан tenant, не публичный gateway. Упомянутый docker-compose.mvvorokov-staging.yml отсутствует. Документация не подтверждает отдельный публичный сервер.

Локальное публичное превью mh-promotion-gateway-review реально healthy на 127.0.0.1:8097. Read-only GET /healthz, /, /pricing, /dlya-proizvoditeley-wb, /calculator, /guides, /robots.txt, /sitemap.xml вернули 200. Этот preview не доказывает работу реальных email/WB/платежей и не является опубликованным выпуском.

## Полный hash-манифест проверенной области

Категории соответствуют таблице выше. Отсутствие пути обозначено «—». Hash — Git blob SHA-1; он не является хэшем персональных данных или секрета.

| Путь | Категория | Сервер | Baseline | Target |
|---|---|---|---|---|
| gateway/.dockerignore | matches_both | ae6ef18ba9b2e2464d288d8993682fd8185e464b | ae6ef18ba9b2e2464d288d8993682fd8185e464b | ae6ef18ba9b2e2464d288d8993682fd8185e464b |
| gateway/Dockerfile | matches_both | 3d41a26f02196007b8bd2de10507cff59a193eb2 | 3d41a26f02196007b8bd2de10507cff59a193eb2 | 3d41a26f02196007b8bd2de10507cff59a193eb2 |
| gateway/admin_routes.py | matches_baseline_only | f655ba0089c5eed2a7623a0357636a5c3dfa4bab | f655ba0089c5eed2a7623a0357636a5c3dfa4bab | 770b8060b26c9bff46b97dc33fd2c2dd6ec4d0b9 |
| gateway/app.py | matches_baseline_only | 90dfd2af25fc4cc3489bb007996db1ae63cce533 | 90dfd2af25fc4cc3489bb007996db1ae63cce533 | 3a3bfb27e5ea3ce011f8012a8bfeec60d75bab51 |
| gateway/billing_routes.py | matches_baseline_only | d8c177d14ee28999902dde342376b3b765c22304 | d8c177d14ee28999902dde342376b3b765c22304 | db920e39fbb36767bd8628405ed8a3e60ccc76c8 |
| gateway/client_ip.py | matches_both | 932b933bbbfd2eb3d9110c949c4a5cbe535cdd23 | 932b933bbbfd2eb3d9110c949c4a5cbe535cdd23 | 932b933bbbfd2eb3d9110c949c4a5cbe535cdd23 |
| gateway/config.py | matches_baseline_only | 3878798eff85189e329e6203153720a3e3ceb9e7 | 3878798eff85189e329e6203153720a3e3ceb9e7 | 1659e36412d3e1df58e9d22eaadcefa60c838a75 |
| gateway/constraints-windows.txt | matches_both | d0a57dac8d2a8ade5f556a8eb40f80e7924841f5 | d0a57dac8d2a8ade5f556a8eb40f80e7924841f5 | d0a57dac8d2a8ade5f556a8eb40f80e7924841f5 |
| gateway/docker-entrypoint.sh | matches_both | 1bc8ef859a56e65dde203242162788725a050dba | 1bc8ef859a56e65dde203242162788725a050dba | 1bc8ef859a56e65dde203242162788725a050dba |
| gateway/growth_internal.py | absent_on_server | — | — | 08e66cc3fbe3e776af1a1e4aa6be75672ec36199 |
| gateway/growth_web.py | absent_on_server | — | — | 2191d96bd29d51402d907e1d168f83bc7f377e6f |
| gateway/internal_auth.py | matches_both | 81f3d15bbdfaa33217ba7a99c623a43d7b0b80b7 | 81f3d15bbdfaa33217ba7a99c623a43d7b0b80b7 | 81f3d15bbdfaa33217ba7a99c623a43d7b0b80b7 |
| gateway/internal_routes.py | matches_both | 306c135bf17398ca418a4ffbcfd59667b1edb071 | 306c135bf17398ca418a4ffbcfd59667b1edb071 | 306c135bf17398ca418a4ffbcfd59667b1edb071 |
| gateway/logging_config.py | matches_both | aa557197148bca1eac074fdf35e87f28a6e8cd3e | aa557197148bca1eac074fdf35e87f28a6e8cd3e | aa557197148bca1eac074fdf35e87f28a6e8cd3e |
| gateway/logic/__init__.py | matches_both | e69de29bb2d1d6434b8b29ae775ad8c2e48c5391 | e69de29bb2d1d6434b8b29ae775ad8c2e48c5391 | e69de29bb2d1d6434b8b29ae775ad8c2e48c5391 |
| gateway/logic/accounts.py | matches_baseline_only | 2f524c1dec7ff550a4187c48d3b5590c06418a90 | 2f524c1dec7ff550a4187c48d3b5590c06418a90 | a5bbd2fedfca178953e78b31426b698b800366f0 |
| gateway/logic/billing.py | matches_baseline_only | 8d0f73988ebbe0d10378982d1dab491db60822da | 8d0f73988ebbe0d10378982d1dab491db60822da | 6dbf8845b6583dab259c19dce4609a64db9fa66c |
| gateway/logic/compliance.py | matches_both | 2b060f4d3504fb767e8017276a55b66bb5db8c02 | 2b060f4d3504fb767e8017276a55b66bb5db8c02 | 2b060f4d3504fb767e8017276a55b66bb5db8c02 |
| gateway/logic/compliance_status.json | matches_both | 4bbdfe396c5107fc2c3f12d130491084c52c2d4f | 4bbdfe396c5107fc2c3f12d130491084c52c2d4f | 4bbdfe396c5107fc2c3f12d130491084c52c2d4f |
| gateway/logic/csrf.py | matches_both | 27be9fca368c12758c193cfc4fa4fc358673dfbe | 27be9fca368c12758c193cfc4fa4fc358673dfbe | 27be9fca368c12758c193cfc4fa4fc358673dfbe |
| gateway/logic/db.py | matches_baseline_only | 739a9f7e652460183b881cf58fc5d18d48353193 | 739a9f7e652460183b881cf58fc5d18d48353193 | 0b1579be724c4cd782a3eed9b42959a1816d3c5d |
| gateway/logic/growth.py | absent_on_server | — | — | c3ce331a302ed330c5fd57cc024035fcce58b096 |
| gateway/logic/password_reset.py | matches_both | ae132a1959b9aa1bb926015876b78c7081995bd0 | ae132a1959b9aa1bb926015876b78c7081995bd0 | ae132a1959b9aa1bb926015876b78c7081995bd0 |
| gateway/mailer.py | matches_both | 9f338a9ed22e906577c20b84097dcdd72b8bca93 | 9f338a9ed22e906577c20b84097dcdd72b8bca93 | 9f338a9ed22e906577c20b84097dcdd72b8bca93 |
| gateway/marketing_routes.py | absent_on_server | — | — | 62bbbb1b55f2b158e9bd7668db46316e907bcc3e |
| gateway/password_breach.py | matches_both | bba5ca4d6c2ecdfb9185301568cc8ae73cdb7e5c | bba5ca4d6c2ecdfb9185301568cc8ae73cdb7e5c | bba5ca4d6c2ecdfb9185301568cc8ae73cdb7e5c |
| gateway/provisioning.py | matches_baseline_only | 75fc5c78123830c3f30a6805d582e2c965e55294 | 75fc5c78123830c3f30a6805d582e2c965e55294 | 2920ad21167af5f218ea22853bbc3219c35bb19d |
| gateway/rate_limiter.py | matches_both | dcbd820d93ea6cb16374a11869336c2d895f9464 | dcbd820d93ea6cb16374a11869336c2d895f9464 | dcbd820d93ea6cb16374a11869336c2d895f9464 |
| gateway/requirements.in | matches_both | 7cc9b463d45d1c9bd4543227519c8c65a28b08c9 | 7cc9b463d45d1c9bd4543227519c8c65a28b08c9 | 7cc9b463d45d1c9bd4543227519c8c65a28b08c9 |
| gateway/requirements.lock | matches_both | 24f000d69b88394bc5390096964c84450039b62b | 24f000d69b88394bc5390096964c84450039b62b | 24f000d69b88394bc5390096964c84450039b62b |
| gateway/requirements.txt | matches_both | 167e4fac331d99cbfa8c8fca75fa53483329daf6 | 167e4fac331d99cbfa8c8fca75fa53483329daf6 | 167e4fac331d99cbfa8c8fca75fa53483329daf6 |
| gateway/sentry_integration.py | matches_both | 5574faea7543c53946f7ddf3de7cc5c476a542a8 | 5574faea7543c53946f7ddf3de7cc5c476a542a8 | 5574faea7543c53946f7ddf3de7cc5c476a542a8 |
| gateway/static/images/og-cover.png | absent_on_server | — | — | 6889f50ac233ce0a0f163f0625858c71342283cd |
| gateway/static/images/shot-control.png | matches_both | c1d3d1a0e00218b48b94386f2105d2ea7bc3df5d | c1d3d1a0e00218b48b94386f2105d2ea7bc3df5d | c1d3d1a0e00218b48b94386f2105d2ea7bc3df5d |
| gateway/static/images/shot-finance.png | matches_both | aea632cff754e939f9c8c2c1dca6b11bf1ebde80 | aea632cff754e939f9c8c2c1dca6b11bf1ebde80 | aea632cff754e939f9c8c2c1dca6b11bf1ebde80 |
| gateway/static/images/shot-margin.png | matches_both | 368aa6496434792841fde72d094cbd2ffe6542aa | 368aa6496434792841fde72d094cbd2ffe6542aa | 368aa6496434792841fde72d094cbd2ffe6542aa |
| gateway/static/images/shot-ranking.png | matches_both | c0a03a768d09e96b674d3937b2c67862c9b7d1ee | c0a03a768d09e96b674d3937b2c67862c9b7d1ee | c0a03a768d09e96b674d3937b2c67862c9b7d1ee |
| gateway/static/images/taiichi-ohno.jpg | matches_both | d1197d9868cbac7028556a19fd1867adbd96d616 | d1197d9868cbac7028556a19fd1867adbd96d616 | d1197d9868cbac7028556a19fd1867adbd96d616 |
| gateway/static/images/tutorial-poster.jpg | matches_both | 6e9acc78ff79bfbb2382c5ffed0622bfef4cb5d0 | 6e9acc78ff79bfbb2382c5ffed0622bfef4cb5d0 | 6e9acc78ff79bfbb2382c5ffed0622bfef4cb5d0 |
| gateway/static/public-analytics.js | absent_on_server | — | — | 27e2f2cb845d8e98f19f3053239bae39d76edd7a |
| gateway/static/videos/tutorial.mp4 | matches_both | ffc48037d6dfd72bfd9a45bf148a6e503c144805 | ffc48037d6dfd72bfd9a45bf148a6e503c144805 | ffc48037d6dfd72bfd9a45bf148a6e503c144805 |
| gateway/telegram_bot.py | matches_both | ddf2e12ccdf7e3196f130df5eecf5364986e2d5b | ddf2e12ccdf7e3196f130df5eecf5364986e2d5b | ddf2e12ccdf7e3196f130df5eecf5364986e2d5b |
| gateway/template_setup.py | absent_on_server | — | — | 3fbd4b72a0fac3ce07547c801ea97de238aa717c |
| gateway/templates/account.html | matches_baseline_only | 8b8164c5dc0b2a96bfa5ae879053f025e15a8d8b | 8b8164c5dc0b2a96bfa5ae879053f025e15a8d8b | e0ca4646ac19b47026632f61c5d2b44d84de0049 |
| gateway/templates/account_confirm.html | matches_baseline_only | 9c0cf850419deb860f10dd18b3d74da2871d9d4c | 9c0cf850419deb860f10dd18b3d74da2871d9d4c | 9a0dab6b649068a3a8b61688def90a000925cf54 |
| gateway/templates/admin_dashboard.html | matches_baseline_only | a10769b33568272740edc4afa03de8bd45c24a2b | a10769b33568272740edc4afa03de8bd45c24a2b | d89e8b131f1e6ce335bd4635a481cf351a39bc20 |
| gateway/templates/base.html | matches_baseline_only | 01ff93517eadddc6f9ceacfb92f0026031aa2641 | 01ff93517eadddc6f9ceacfb92f0026031aa2641 | 853e162dac0b689a56b9486033ebeb5809ebabfb |
| gateway/templates/billing.html | matches_baseline_only | 1d5c82798b95b24ef2c9459ae32a576e48c8ff56 | 1d5c82798b95b24ef2c9459ae32a576e48c8ff56 | 1f51c0e8727ac547334324e6f1c8eba42c4f2444 |
| gateway/templates/billing_cancel.html | matches_baseline_only | 928e300487516769fcbb50d8382068db050eda2c | 928e300487516769fcbb50d8382068db050eda2c | 39e2ae4102bca3dcb12dbdec8b41d5cbeafd941a |
| gateway/templates/billing_return.html | matches_baseline_only | 75dac5250f47b715e4c46effdae01d6d85bdbf95 | 75dac5250f47b715e4c46effdae01d6d85bdbf95 | ce89c82334aeb156de9aee90d7cdb72ab37e2552 |
| gateway/templates/compliance.html | unique_server | 02a1dba6c91ee71360eea6be1a585247e1093311 | 0bb906ba65f0deee4aad48a18d3e99957b5e88ec | b38325ed3d88b41f62cf184e42ac6669969e1c80 |
| gateway/templates/contacts.html | unique_server | b26dd1d1a991ce95cb5f0d553cdc5ae8557858c1 | 21c02d67412a9d00cf503505254e6f34c5852127 | 5ab9c916e2a804b6b896bf2c779e357246f2ab43 |
| gateway/templates/forgot_password.html | matches_baseline_only | 98f3d2088bc0f997b310397a9b6cc5831c0f2036 | 98f3d2088bc0f997b310397a9b6cc5831c0f2036 | 8913360854d381a2cb282005dae291019148bff4 |
| gateway/templates/home.html | matches_baseline_only | c000a8f31a1bb3b9979695f95d8bbb5ec61bd83e | c000a8f31a1bb3b9979695f95d8bbb5ec61bd83e | 450a50c1f726d3199ef16838069aac18dfdf99e0 |
| gateway/templates/login.html | matches_baseline_only | 13c7394285c908ac223e21bba336b8b2c5cb40c1 | 13c7394285c908ac223e21bba336b8b2c5cb40c1 | a51257e00ee2c4c9a92b17e30acd2ef328713bdd |
| gateway/templates/marketing_base.html | absent_on_server | — | — | 494af09a2a09f3ce17cbafe66ccabc07c65af6ee |
| gateway/templates/marketing_calculator.html | absent_on_server | — | — | 48440129e7cabf8ac1c776d1b5a5aaa4e192e901 |
| gateway/templates/marketing_calculator_page.html | absent_on_server | — | — | 586f17d688d61f544a82c167b87b1f514b5a67d4 |
| gateway/templates/marketing_cost.html | absent_on_server | — | — | d981fa58505e25147c3d6e14f2adbb63cf84f184 |
| gateway/templates/marketing_fifo.html | absent_on_server | — | — | 5644e93515c4f0b3ef3bf899538f70391185f730 |
| gateway/templates/marketing_guide_excel.html | absent_on_server | — | — | 62cc7bd7f4976d107bc692147ef265c5515c39cb |
| gateway/templates/marketing_guide_fifo.html | absent_on_server | — | — | 077ed72bbe14edc20e6a628aece6d8bc44794275 |
| gateway/templates/marketing_guide_profit.html | absent_on_server | — | — | 1cf23c534b79617e42c33f30f2a52263d4ff722b |
| gateway/templates/marketing_guides.html | absent_on_server | — | — | df16f420baf286d6204aff29b6a3ad475e12743f |
| gateway/templates/marketing_producers.html | absent_on_server | — | — | e0fd573ba81809322e22dc5141e6b57545d9f59b |
| gateway/templates/offer.html | unique_server | 93c40ca51316423c242e3249bccfaaadeaeb895e | bec0a2d63c6e2b5dba8de1fa501c92f931646826 | a135d967387c09338a8fba5527edb600db05ef0e |
| gateway/templates/pricing.html | matches_baseline_only | 211765f4cc44e550e6e5354b684a9155bd8de946 | 211765f4cc44e550e6e5354b684a9155bd8de946 | 1709df10a7828f80a50dc2292a88dca5366dd3f2 |
| gateway/templates/privacy.html | matches_baseline_only | 8f332659e6e075bb39c2b6ed8bf8c4b3f221459d | 8f332659e6e075bb39c2b6ed8bf8c4b3f221459d | 40ae28e873ff6b137b6f2a3da5cee9ba414f8c19 |
| gateway/templates/provisioning.html | matches_baseline_only | 9b79f7b1b2a2c4bb46cdde23cc5e3566a3b4fbb7 | 9b79f7b1b2a2c4bb46cdde23cc5e3566a3b4fbb7 | 482c7b49aaa476a11623e24525af56fea184f0e9 |
| gateway/templates/register.html | matches_baseline_only | 946296ba0f0277d517f9a92c4b40bf04fc19a475 | 946296ba0f0277d517f9a92c4b40bf04fc19a475 | ef4edb7deac0d1340709b703b7e5e4cadd465f99 |
| gateway/templates/reset_password.html | matches_baseline_only | 811dd958e8243a051a3351d90fb0c9e05701821a | 811dd958e8243a051a3351d90fb0c9e05701821a | 98852ffc5d5210337e164df42680516fc256dba6 |
| gateway/templates/verify_email_sent.html | matches_baseline_only | 9a3a37946e0b518e65ae2bb1240844858ba69dc5 | 9a3a37946e0b518e65ae2bb1240844858ba69dc5 | 8d9ad8bc98e25be3ad2e792a264fad1bffc115f1 |
| gateway/tests/__init__.py | matches_both | e69de29bb2d1d6434b8b29ae775ad8c2e48c5391 | e69de29bb2d1d6434b8b29ae775ad8c2e48c5391 | e69de29bb2d1d6434b8b29ae775ad8c2e48c5391 |
| gateway/tests/base.py | matches_both | 191f9855c9c8d3a9b0faf62a5c5fb3350200eb67 | 191f9855c9c8d3a9b0faf62a5c5fb3350200eb67 | 191f9855c9c8d3a9b0faf62a5c5fb3350200eb67 |
| gateway/tests/conftest.py | matches_both | b21b31ad1aeea145c4264fe4100d7c605197d557 | b21b31ad1aeea145c4264fe4100d7c605197d557 | b21b31ad1aeea145c4264fe4100d7c605197d557 |
| gateway/tests/test_account_routes.py | absent_on_server | — | 550a2c97611a630039855ab0c6b1dd50ea17382c | 550a2c97611a630039855ab0c6b1dd50ea17382c |
| gateway/tests/test_accounts.py | unique_server | a63b7c102e017853eabdb81a95f371468a2aefa2 | 0d2367532d245fafd49f8c9946c5da25b5dda3aa | 0d2367532d245fafd49f8c9946c5da25b5dda3aa |
| gateway/tests/test_admin.py | matches_both | 65e571228fb937396d1f2e478c41b81c11b493bc | 65e571228fb937396d1f2e478c41b81c11b493bc | 65e571228fb937396d1f2e478c41b81c11b493bc |
| gateway/tests/test_auth_form_csrf_errors.py | matches_both | 6d406a15b2f1829939259bd3f4056ee4954d2b13 | 6d406a15b2f1829939259bd3f4056ee4954d2b13 | 6d406a15b2f1829939259bd3f4056ee4954d2b13 |
| gateway/tests/test_backup_tenant_volumes_script.py | absent_on_server | — | 9ad2f7532237f27ae12e09b3ee7e96937d338d10 | 9ad2f7532237f27ae12e09b3ee7e96937d338d10 |
| gateway/tests/test_billing.py | unique_server | 86e51ce1fb29f0cf1385aa8550e3ff513a68bef3 | 525b35f651bc410d6798716de2dbffd67001ed4f | 525b35f651bc410d6798716de2dbffd67001ed4f |
| gateway/tests/test_billing_routes_webhook.py | unique_server | e10e70157edfa5f8718cb048481239c26cf0d875 | 9503aeac99c90271b6249e8c99a7ad52e99d5961 | 9503aeac99c90271b6249e8c99a7ad52e99d5961 |
| gateway/tests/test_ci_contract.py | absent_on_server | — | — | 581c963174af1a4da840e2a80a97110377afe23b |
| gateway/tests/test_client_ip.py | matches_both | e157417dd7ebdc79b6bce8be9b7c9e99b9c805e7 | e157417dd7ebdc79b6bce8be9b7c9e99b9c805e7 | e157417dd7ebdc79b6bce8be9b7c9e99b9c805e7 |
| gateway/tests/test_compliance_status.py | matches_both | ace4956e24f2972ff1d1f35e92d1c1982f0761d8 | ace4956e24f2972ff1d1f35e92d1c1982f0761d8 | ace4956e24f2972ff1d1f35e92d1c1982f0761d8 |
| gateway/tests/test_config.py | matches_both | 14a1f7a494951ab6706e494d6f587135192a5572 | 14a1f7a494951ab6706e494d6f587135192a5572 | 14a1f7a494951ab6706e494d6f587135192a5572 |
| gateway/tests/test_csrf.py | matches_both | 21003715ffc1e986e601ad1f65a666fa2d455ee7 | 21003715ffc1e986e601ad1f65a666fa2d455ee7 | 21003715ffc1e986e601ad1f65a666fa2d455ee7 |
| gateway/tests/test_db_backend.py | matches_both | b6c9b39c72106ac489b234e9e4f7ebb8fb1605b0 | b6c9b39c72106ac489b234e9e4f7ebb8fb1605b0 | b6c9b39c72106ac489b234e9e4f7ebb8fb1605b0 |
| gateway/tests/test_db_backend_postgres.py | matches_both | 96315f109ede542db42918f5ab458f0400f1cbb0 | 96315f109ede542db42918f5ab458f0400f1cbb0 | 96315f109ede542db42918f5ab458f0400f1cbb0 |
| gateway/tests/test_email_verification_routes.py | absent_on_server | — | 75ef56477c7bc4c0d2ad0659e1b541075ae4be66 | 75ef56477c7bc4c0d2ad0659e1b541075ae4be66 |
| gateway/tests/test_growth.py | absent_on_server | — | — | b26616ddaaf5e97e1a18d582d7af9325c903c25d |
| gateway/tests/test_growth_admin.py | absent_on_server | — | — | 687360a0d2d29c0605f14599ff2596f3464ae3c8 |
| gateway/tests/test_growth_integration.py | absent_on_server | — | — | 84354dbae8f8dcbde1deea9f69efb7363a36b494 |
| gateway/tests/test_growth_postgres.py | absent_on_server | — | — | b8ed86c47ab5f1272b7bc13f0efc4f79ddde3156 |
| gateway/tests/test_growth_provisioning.py | absent_on_server | — | — | 2273c486bf398f979b0bb63bb4e5679069595181 |
| gateway/tests/test_health.py | absent_on_server | — | fc66cea51500dadc23b3b069885b24ec266c5b6a | fc66cea51500dadc23b3b069885b24ec266c5b6a |
| gateway/tests/test_internal_auth.py | matches_both | efb7b3d4a7d0525772b3866349d3b0c1a9987c66 | efb7b3d4a7d0525772b3866349d3b0c1a9987c66 | efb7b3d4a7d0525772b3866349d3b0c1a9987c66 |
| gateway/tests/test_internal_routes.py | unique_server | db6e40a3d55be5e64e6508af664ac890164baccc | 2661c6c349c78b0ff26fc28f2ffe597172b3a997 | 2661c6c349c78b0ff26fc28f2ffe597172b3a997 |
| gateway/tests/test_logging_config.py | matches_both | eeba7a1889d93a57f9597a132e4d7186272f65d1 | eeba7a1889d93a57f9597a132e4d7186272f65d1 | eeba7a1889d93a57f9597a132e4d7186272f65d1 |
| gateway/tests/test_mailer.py | matches_both | 58bc83ecd0caaefc3377ee4ccef4614b36cbb192 | 58bc83ecd0caaefc3377ee4ccef4614b36cbb192 | 58bc83ecd0caaefc3377ee4ccef4614b36cbb192 |
| gateway/tests/test_marketing_calculator.cjs | absent_on_server | — | — | 586529350d85cf27b699740786a46c9c9490e71a |
| gateway/tests/test_marketing_routes.py | absent_on_server | — | — | 0c5bfa39261e08b1349a802d9ea41a1ba41c6682 |
| gateway/tests/test_password_breach.py | matches_both | 935f95aa1cb7e50efc384a53e7529051a6152738 | 935f95aa1cb7e50efc384a53e7529051a6152738 | 935f95aa1cb7e50efc384a53e7529051a6152738 |
| gateway/tests/test_password_policy_routes.py | unique_server | c7eec60a9b2be5158f00c2d956140899e88db75f | 0741d067c22fea85157f56ec5a0bcc49cd6fd724 | 0741d067c22fea85157f56ec5a0bcc49cd6fd724 |
| gateway/tests/test_password_reset.py | matches_both | fed14ad3b8ba0d549292966d58de92416350b23b | fed14ad3b8ba0d549292966d58de92416350b23b | fed14ad3b8ba0d549292966d58de92416350b23b |
| gateway/tests/test_provisioning_credentials.py | unique_server | 652c35ff5ab909594862f34d155eccdf90e2e82b | 8dde81bd232251d5933755ee700f59084c74b9e5 | 8dde81bd232251d5933755ee700f59084c74b9e5 |
| gateway/tests/test_public_analytics.cjs | absent_on_server | — | — | 562f4c7bdb6ec77c796867bdcafdea2f2d8730f8 |
| gateway/tests/test_public_privacy_assets.py | matches_both | a822ff394b741acbbefb07d0f56ce4d1dbdf334c | a822ff394b741acbbefb07d0f56ce4d1dbdf334c | a822ff394b741acbbefb07d0f56ce4d1dbdf334c |
| gateway/tests/test_rate_limiter.py | unique_server | 5e5f053f2c940ec591279e054e16ddd48191f8e7 | c3ab6d33e4b629c30030cfd3283e26fe32b67f97 | c3ab6d33e4b629c30030cfd3283e26fe32b67f97 |
| gateway/tests/test_sentry_integration.py | matches_both | 850458a8354e6748b3e1bb0bce5b5ecebfe4597f | 850458a8354e6748b3e1bb0bce5b5ecebfe4597f | 850458a8354e6748b3e1bb0bce5b5ecebfe4597f |
| gateway/tests/test_seo.py | absent_on_server | — | — | ad984899522a24cfb4d777d38b91bc45c09151da |
| gateway/tests/test_telegram_bot.py | matches_both | 96bafe75feba50b0a4ebe5765f6a4dc78ed04fb5 | 96bafe75feba50b0a4ebe5765f6a4dc78ed04fb5 | 96bafe75feba50b0a4ebe5765f6a4dc78ed04fb5 |
| gateway/tests/test_telegram_link.py | matches_both | 14069102a6bc397a6715d1d98c895aa62f44c47b | 14069102a6bc397a6715d1d98c895aa62f44c47b | 14069102a6bc397a6715d1d98c895aa62f44c47b |
| gateway/tests/test_tenant_memberships.py | absent_on_server | — | 282760ef4475cb0854a68f941c4158d6611be5fd | 282760ef4475cb0854a68f941c4158d6611be5fd |
| gateway/tests/test_yookassa_client.py | absent_on_server | — | 5c8cf124453111edaa81ad8656cc415451b46069 | 5c8cf124453111edaa81ad8656cc415451b46069 |
| gateway/yookassa_client.py | matches_both | a244f13e6101b1c98c0379f948010cdf2bd6c867 | a244f13e6101b1c98c0379f948010cdf2bd6c867 | a244f13e6101b1c98c0379f948010cdf2bd6c867 |
| scripts/run_billing_cycle.py | matches_baseline_only | a2cde069f32d23965ebb9da5fc00fb8a5acc718b | a2cde069f32d23965ebb9da5fc00fb8a5acc718b | 35fbc1e4e3ececcd28fb2e9d94c89093a4132be2 |
