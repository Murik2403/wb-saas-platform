# Аудит MARKETSHELPER — подтверждённый статус на 15 октября 2026

Текущий production HEAD — **`73c6a34b8ee2940a25fe40ac8c65c2e06672c02d`**: ветки Claude по PDF-бюджету и резервированию уроков объединены с `89ad911` и выложены на все пять tenants, gateway и provisioner. Финальная приёмка: 03:16:59 МСК. [Подробный акт новой выкладки](PDF_LESSONS_PRODUCTION_ACCEPTANCE_2026-10-15.md): два общих PDF-слота, боевой encrypted backup/restore всех 16 уроков, S3 readback, daily/hourly integration и freshness PASS. Строгие 25/30 и независимая новая ОС пока открыты.

Следующие разделы 1–4 и их исходные акты описывают **предыдущую** выкладку оптимизации 3dc30c3. Её образы заменены текущей ревизией выше; исторические результаты и отрицательные акты сохранены.

Оптимизация `89ad911a3a5d5a88d49991f0369b6bb858283bc6` выложена на все пять кабинетов из закоммиченного HEAD `3dc30c3c2594535178918822f29f8235ce126010`. Реальная загрузка исходного зашифрованного архива образов пройдена. Эти два пункта закрыты; весь аудит и восстановление сервера на новой ОС этим актом не закрываются.

Время итоговой проверки production: **15.10.2026 01:42:14 МСК** (`2026-10-14T22:42:14Z`). Ниже отдельно указаны новые результаты и границы исторических актов.

## 1. Выполненная выкладка

Собран минимальный образ поверх каждого фактического исходного образа кабинета. Из `git archive` указанного HEAD перенесены три Python-модуля: `tenant-app/calculations.py`, `tenant-app/ui_helpers.py`, `mh-redesign-lab/tenant-api/api_server.py`. Зависимости и frontend не пересобирались. Проверены наследуемые слои, пользователь контейнера, SHA модулей и frontend; изменён только ожидаемый `build-id.txt`.

| Кабинет | Immutable image после выкладки | Проверка |
|---|---|---|
| demo | `sha256:05b11778bc9b5b49e826d404865a9a4b711791d0f75f82cc9ac615127eb2ae1b` | HEAD, public/signed health, frontend SHA |
| mvvorokov | `sha256:05b11778bc9b5b49e826d404865a9a4b711791d0f75f82cc9ac615127eb2ae1b` | HEAD, public/signed health, frontend SHA |
| marketing | `sha256:694047c37775127b187ea3655f1eac36cbd8b36464a16c91879e598ab073ad90` | HEAD, public/signed health, frontend SHA |
| letoocean | `sha256:e3da4c14f4dbe194171565673366dd9178341128e99b431f614bf464dfb749ee` | HEAD, public/signed health, frontend SHA |
| alexdalex-msk | `sha256:d85472c79ecfad3d37941f06c28ba78bd3242cc1d62c653409b0bc309e737752` | HEAD, public/signed health, frontend SHA |

Для каждого кабинета до первой замены сделана свежая сохраняемая зашифрованная копия; candidate и rollback восстановлены и проверены на изолированных копиях реальных данных без сети. Старые контейнеры оставлены для отката. Том, aliases, credentials/keyrings, лимиты и restart policy сохранены. Из tenant Env изменён только `REDESIGN_BUILD_ID`.

Gateway и provisioner сохранили код и исходные immutable образы ревизии `b13fa97edafa81d814ab1464a8e979abc85e7a62`. Они пересозданы только для закрепления `WB_SAAS_TENANT_IMAGE=mh-tenant:audit-3dc30c3-36451b45456f`. Проверены действующая Compose-конфигурация, обе runtime-переменные и доступ gateway к `http://provisioner:8001/healthz`. Обновление pin выполняет отдельная транзакция с предварительной валидацией, приватной копией конфигурации и обратным откатом; тесты включают отказы на промежуточных шагах и конкурентное изменение `.env`.

## 2. Проверки результатов и поведения

- Fresh tenant-app: **1233 passed, 1 skipped, 62 subtests passed**; tenant-api: **678 passed**, оба exit 0.
- Независимая сверка с исходными формулами: **576/576** точных совпадений скалярных проекций итогов операций на синтетических случаях (48 вариантов × 3 окна × 4 идентификатора); это не сравнение полных API-ответов, реальные данные этим тестом не читались.
- На одинаковых восстановленных production-снимках полные ответы candidate и rollback совпали по SHA: **16 сравнений** — demo 5, mvvorokov 5, остальные кабинеты по 2. Проверены магазинный P&L, помесячный P&L и по три SKU в demo/mvvorokov. В остальных копиях подходящих SKU для выборки не было; все товары этим актом не приняты.
- На каждой из пяти копий **32 GET data-quality в четырёх сериях по восемь параллельных** дали 200; guard сохранён. Проверены неизменность защищённых таблиц и стабильность basis FIFO guard на копии.
- На production mvvorokov: финансы за 90 дней — **200, 6,473 с**, период подтверждён. Первое чтение data-quality — **3,297 с**; последующие 8 параллельных запросов — **8/8 HTTP 200**, максимум **0,810 с**. Хэши live-таблиц при этом потоке не сверялись: проверка неизменности таблиц относится к изолированным копиям.
- Новых OOM и рестартов не обнаружено. Лимиты tenants 1536 MiB, gateway 1024 MiB, provisioner 256 MiB сохранены. Capacity видит 5 tenants, reservation 1920 MiB, допускает следующий; запрос без авторизации отклонён.
- Существующие **16 уроков, субтитры, Range и security headers** сохранены. Это проверка сохранности текущей публикации, а не завершение новой работы Claude по резервированию уроков.

Первая попытка оператора остановилась **до замены live-контейнеров**: manifest ожидал старый SHA `build-id.txt`. Неизменность семи live ID/images подтверждена отдельно. Исправлена только ожидаемая ревизия manifest; отрицательный акт первой попытки сохранён. Формулы, guard и требования проверки не ослаблялись.

## 3. Реальное восстановление архива образов

Прогон `30208ac466a5` загрузил исходный аутентифицированный encrypted escrow в пустой собственный Docker с единственным вспомогательным образом, изоляцией сети и конечными watchdog. Образы приложения предварительно не копировались и контейнеры приложения не запускались.

Проверены **8 уникальных immutable image ID / 9 ролей**, точные количества и canonical SHA слоёв RootFS для каждой роли. Переданный plaintext-поток совпал с ожидаемым размером и SHA; `docker load` завершился успешно. Инвентаризация с `--all` показала полный точный набор без лишних image ID, кроме разрешённого helper. Распакованное собственное хранилище — 1 764 022 086 байт при лимите 4 GiB.

Причина предыдущего ложного отказа: обычный `docker image ls` не показывал загруженные untagged images. Исправлена инвентаризация, а проверка точного image ID/RootFS сохранена. Все три прежних отрицательных акта оставлены.

Собственные containers/volumes/networks/guard units удалены, независимая итоговая проверка показала **0 остатков**. На время отдельного image-теста исходные production ID/images/mounts и lifecycle/OOM-счётчики не изменились. Последующая разрешённая выкладка учитывается отдельным актом выше.

**Граница:** проверен архив исходных образов до этой выкладки (tenant runtime `09e414a`, control plane `b13fa97`), а не escrow новых образов `3dc30c3`. Этот запуск не проверяет запуск приложений из загруженных образов, новую ОС, новый DNS/TLS или полный server DR. Проверка исходных криптографических данных и linked stack относится к отдельному историческому акту 14 октября.

## 4. Дополнительные закрытые пункты

На хосте четыре подтверждённых root-owned regular файла имели mode 0666: основной Compose, `scripts/backup_tenant_volumes.py`, два Traefik YAML. После сверки типа/владельца/SHA убрана только запись для group/other: **0666 → 0644**. Содержимое, UID/GID и доступ на чтение сохранены, Compose проверен до и после. Перезапусков служб ради изменения mode не было.

РКН: публичная [запись оператора №77-26-574895](https://pd.rkn.gov.ru/operators-registry/operators-list/?id=77-26-574895) проверена напрямую на портале 15.10.2026 (HTTP 200). Оператор — ИП Вороков Мурат Валерьевич; внесён в реестр приказом №596 от **12.10.2026**, дата регистрации уведомления — 11.10.2026. Подача уведомления и включение в реестр подтверждены, этот пункт аудита закрыт. Ранее предоставленный номер уведомления №100456256 отличается от регистрационного номера оператора. Ключ проверки не переносится в публичные документы. Telegram остаётся временно выключенным по решению владельца.

## 5. Оставшиеся gates

| Пункт | Подтверждено сейчас | Что требуется для закрытия |
|---|---|---|
| Восстановление сервера | Исторически: 21 SQLite / 777 экземпляров таблиц, исходная криптография и 58 API/auth gates до и после перезапуска собственного Docker; 45 host config файлов и офлайн TLS-входы. Теперь также реальный image-load | Независимая новая ОС/VM, boot/reboot, host jobs, новый DNS/TLS handshake, UI и вход. DinD на прежнем хосте это не заменяет |
| Плотность 20–30 | 20 прошли полный функциональный фон/combined; HTTP-фазы 25/30 прошли | Строгие kernel memory.peak и весь simultaneous background/combined. Для 25 фон отказал (помещалось 19), для 30 — 18. Диагностический обход budget только в собственном namespace не разрешает 30 production tenants |
| Уроки | Боевой encrypted backup/restore 16 MP4/VTT, 32 SHA, isolated gateway, Range/VTT, S3 readback и cron/helper/watchdog проверены на 73c6a34 | Пункт закрыт в пределах [акта](PDF_LESSONS_PRODUCTION_ACCEPTANCE_2026-10-15.md); первое будущее срабатывание нового cron отдельно от ручной проверки его функций |
| PDF-бюджет | На всех пяти tenants healthy slots=2, Linux flock/crash/fail-closed и durable queue/leases проверены | 25/30 всей нагрузкой; real provider/delivery. Свежая crash lease ждёт до 300 с, secondary manual busy требует повтора |
| Ключи восстановления | Аутентификация текущей owner-копии и offsite ciphertext проверены | Независимая копия ключей вне рабочего ПК и восстановление без первой копии |
| YooKassa | Автоматические/смоделированные проверки | Test-shop, настоящий тестовый платёж, частичный возврат, webhook/чек/пропорциональный срок |
| Ozon | Реализация и тесты | Непустые данные после отгрузки; отдельные Performance credentials для рекламы. Владелец отложил этап |
| Налоги | Реализация и тесты | Приёмка бухгалтером оснований/режимов/дат/НДС/зачётов/чеков |
| Mobile | Историческая Android/WebView проверка | Physical device, настоящий вход, production signing/store accounts; для iOS Mac/Xcode/Team ID; push отдельно |

Unknown старые архивы и Defender не переоткрываются. Ранее заблокированное удаление конкретных private temp folders не обходилось и secure erasure не объявляется. Проверенный migration tooling SQLite→PostgreSQL не означает выполненный production cutover.

## 6. Доказательства

Ниже локальные акты с техническими метаданными и SHA; секреты и финансовые значения в них не публиковались. Пути относительно каталога этой checkout.

- [Выкладка и pin-транзакция](../../deliveries/optimization-release-20261015/deployment-proof.json), [итоговый production gate](../../deliveries/optimization-release-20261015/final-acceptance-proof.json), [readback SHA](../../deliveries/optimization-release-20261015/proof-readback.json).
- [Fresh тесты](../../deliveries/optimization-release-20261015/independent-test-acceptance.json), [576 сравнений](../../deliveries/optimization-release-20261015/independent-parity-proof.json), [сверка production-копий](../../deliveries/optimization-release-20261015/copied-financial-proof.json), [конкурентность копий](../../deliveries/optimization-release-20261015/copied-concurrency-proof.json).
- [Fault tests pin-транзакции](../../deliveries/optimization-release-20261015/pin-transaction-fault-proof.json), [отрицательная первая попытка](../../deliveries/optimization-release-20261015/operator-result-attempt1.json), [исправление manifest](../../deliveries/optimization-release-20261015/build-id-gate-correction.json).
- [Реальный image-load](../../deliveries/image-load-acceptance-20261015/image-load-owner-proof-01304705436e40e4a23df6e1e1953281.json), [независимая очистка](../../deliveries/image-load-acceptance-20261015/independent-final-cleanup.json).
- [Устранение world-write](../../deliveries/audit-reconcile-20261015/world-write-hardening-proof.json), [сверка объёма аудита](../../deliveries/audit-reconcile-20261015/report.md).
- [Историческая ночная приёмка](AUDIT_SHARED_HOST_NIGHT_2026-10-14.md), [исходный статус 13 октября](AUDIT_CURRENT_STATUS_2026-10-13.md).

## Сообщение Claude

Claude, обе ветки `claude/lessons-backup-20261018` (69e0fbe) и `claude/pdf-budget-20261019` (eaecdba) включены в `codex/cabinet-ux-20260930`. Production — `73c6a34b8ee2940a25fe40ac8c65c2e06672c02d`, все пять tenants + gateway/provisioner; будущий tenant pin `mh-tenant:pdf-73c6a34-05b11778bc9b`. До следующих правок включи текущую ветку: добавлены fencing lease owner/attempt, защита active lease, poison-cycle limit, secondary busy deferral/config, fail-closed slots, безопасный purge shared mount и nightly helper в образе provisioner (gateway не содержит cryptography). Боевой restore/S3/cron уроков принят, старые tip повторно не выкатывать. Строгие 25–30, новый OS/boot/DNS/TLS и escrow новых images пока не приняты; независимая owner-копия ключей остаётся открытой. Публичная запись РКН №77-26-574895 проверена напрямую, приказ №596 от 12.10.2026; подача и включение в реестр закрыты. Telegram выключен.
