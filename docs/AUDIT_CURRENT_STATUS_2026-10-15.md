# Аудит MARKETSHELPER — подтверждённый статус на 15 октября 2026

Текущий production runtime всех пяти кабинетов — **`d0141b2220242f003f00edf8d88bd94b6202d98f`**, gateway — **`25ae99a3a148d041ed4a24d587bca6389cd858c3`**, provisioner — **`73c6a34b8ee2940a25fe40ac8c65c2e06672c02d`**. Финальный tenant gate — 15:34:29 UTC. Последняя gateway-выкладка завершилась **20:26:37 МСК / 17:26:37 UTC**: удалён весь блок хранения данных с публичной страницы, исправлены только цвета трёх карточек главной в светлой теме; текст главной не менялся. Остальные кабинеты и provisioner сохранены. [Акт продолжения аудита](AUDIT_FOLLOWUP_2026-10-15.md).

Публичная [страница обработки данных](https://marketshelper.ru/compliance) сохраняет запись РКН **77-26-574895** с прямой официальной ссылкой; раздел хранения, его навигация и инфраструктурные карточки удалены по указанию владельца. Независимая проверка **живого сайта 25ae99a**: 8 сочетаний ширины 320/390/600/1440 px и светлой/тёмной темы, обе страницы (16 просмотров), горизонтальное переполнение 0, JavaScript page errors 0, три scenario-card, минимальный измеренный контраст текста/ссылок 6,071. Gateway image — `sha256:ec1dca37b79bda5af5d26fcb5b8e3a6336b90f8e5d0d339f7deefdadf2231560`; retained rollback и свежий encrypted control-plane backup сохранены. SMTP authentication/NOOP 250, письмо не отправлялось. Акты: `compliance-storage-removal-light-fix/release-proof-f7f3186425e54d8dbfd6946ac870888c.json`, `live-6fd7dd8473/live-proof.json` в delivery аудита.

В d0141b2 устранено избыточное удержание финансовых payload при сборке отчётов: проекция всех прямых чтений метрик и потоковое чтение FIFO с сохранением исходных данных для всех финансовых bindings/projections/history. Формулы не менялись. На изолированной реальной SQLite-копии все 21 title/summary и PNG совпали с исходными, полный PDF с 1024 MiB балластом прошёл без нового OOM при прежнем лимите 1536 MiB. Это отдельная ограниченная проверка памяти, не приёмка 20–30 кабинетов. До замены каждого кабинета сохранён свежий encrypted backup; все семь замен tenant/control подтверждены, rollback сохранён. Frontend, mounts, aliases и лимиты сохранены.

| Текущий кабинет | Immutable image d0141b2 |
|---|---|
| demo, mvvorokov | `sha256:ed2e8b9112a771f8af84e00531616a2935962597e9bfaae83a3f743b8e3a19d4` |
| marketing | `sha256:b579582e9533989bd5b440e8e64332e908268d1d72e076ea5a190631907696c3` |
| letoocean | `sha256:93c4a6cefadf542872c4ab6ba1cde4a7f74cf1da9d43518f0a382527b11b6785` |
| alexdalex-msk | `sha256:61347d5be344617b3391198f8ce35097dc27b1dc0b001f9ae49d072fb2409096` |

Тесты d0141b2: tenant-app + agents **1292 passed, 1 skipped, 62 subtests**; tenant-api **685 passed**. Gateway33: **676 passed, 243 subtests**; отдельный rollout-оператор после исправления наблюдателя backup mount — **91 pure test**. Подтверждено фактическое плановое резервирование семи групп, включая training-media, с offsite/freshness PASS. Owner-копия **8 images / 9 ролей** состава d0141b2/controls73 аутентифицирована, её offsite copy/readback подтверждён. После gateway25ae99a этот архив исторический: новый gateway image требует отдельного capture/authentication/load/offsite; реальная загрузка архивного состава в новый изолированный guest Docker пройдена: 8 точных OCI root IDs, 9 новых role aliases, linux/amd64 child/RootFS совпадают с source witness. Отдельная Ubuntu VM действительно загрузилась; первая установка Docker завершилась отказом загрузки пакетов, тот guest штатно остановлен без клиентских данных. Повторный Ubuntu guest действительно установил Docker 29.7.2; private restrict=on блокирует TCP, внешний UDP DNS и DNS QEMU с теми же целями успешной public-проверки. Прежний loader отказал до загрузки из-за семи legacy projection rows против восьми OCI root IDs; negative сохранён. Новый OCI loader независимо проверен (134 теста и 22 подтеста); actual загрузка **PASS** (`deliveries/local-dr-e1000-20261015/evidence/current-image-load-oci-proof.json`, SHA `9afe3b6df0f4cb4190093a4755dfe0f26b8b72af94d3e078252871e06ff15224`). Проверен 291 descriptor; 24 отсутствующих foreign-platform descriptor явно не восстановлены. Контейнеры приложений не запускались, owner RSA не загружался. Родитель отдельно прочитал inventory 8 images/0 containers и штатно выключил только свою VM; PID вышел с кодом 0, loopback-порты закрыты. Полное восстановление данных/приложений и их запуск после reboot ОС ещё не проведены. Полное восстановление сервера остаётся открытым.

Предыдущая production-ревизия **73c6a34** включила ветки Claude по PDF-бюджету и резервированию уроков вместе с `89ad911`; её финальная приёмка — 03:16:59 МСК. [Подробный акт](PDF_LESSONS_PRODUCTION_ACCEPTANCE_2026-10-15.md): два общих PDF-слота, боевой encrypted backup/restore всех 16 уроков, S3 readback, daily/hourly integration и freshness PASS. Эти функции сохранены последующей минимальной выкладкой d0141b2.

Следующие разделы 1–4 и их исходные акты описывают **предыдущую** выкладку оптимизации 3dc30c3. Её образы заменены текущей ревизией выше; исторические результаты и отрицательные акты сохранены.

Оптимизация `89ad911a3a5d5a88d49991f0369b6bb858283bc6` выложена на все пять кабинетов из закоммиченного HEAD `3dc30c3c2594535178918822f29f8235ce126010`. Реальная загрузка исходного зашифрованного архива образов пройдена. Эти два пункта закрыты; весь аудит и восстановление сервера на новой ОС этим актом не закрываются.

Время итоговой проверки production: **15.10.2026 01:42:14 МСК** (`2026-10-14T22:42:14Z`). Ниже отдельно указаны новые результаты и границы исторических актов.

## 1. Историческая выкладка 3dc30c3

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

## 2. Исторические проверки 3dc30c3

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
| Восстановление сервера | Исторически: 21 SQLite / 777 экземпляров таблиц, исходная криптография и 58 API/auth gates; 45 host config файлов, офлайн TLS-входы и исходный image-load. Теперь чистая отдельная Ubuntu 24.04.5 VM действительно загрузилась: kernel 6.8.0-142-generic, около 4 GiB RAM, без Docker и клиентских данных в акте boot | Docker bootstrap, загрузка текущих images, восстановление данных/config/jobs, boot/reboot, новый DNS/TLS handshake, UI и вход. Чистый boot и DinD не закрывают full DR |
| Образы | Owner-копия d0141b2/controls73: MHOFFSITE2 аутентификация и offsite copy/readback, 456082432 байт исходного потока, 8 images / 9 ролей; plaintext на диск не писался, прежний escrow не менялся. После gateway33 состав уже исторический | `docker load` этого архива ещё проверяется; gateway33 требует отдельного capture/load/offsite. Инвентарь приложения не охватывает весь хост, включая Postgres/Glitchtip |
| Плотность 20–30 | Исторически 20 прошли функциональный фон/combined; HTTP-фазы 25/30 прошли. Новый queue-aware профиль на d0141b2 для 20/25/30 — **NOT RUN** | Строгие kernel memory.peak и весь simultaneous background/combined. Для 25 фон отказал (помещалось 19), для 30 — 18. Queue-aware PASS не заменяет эти отрицательные результаты. Старый27070ca не слит; новый98e3d5f закрывает прежние пропуски, но независимый review воспроизвёл проблемы ownership тома, ложной очистки и позднего admission |
| Уроки | Боевой encrypted backup/restore 16 MP4/VTT, 32 SHA, isolated gateway, Range/VTT, S3 readback и cron/helper/watchdog проверены на 73c6a34; фактический daily запуск 15 октября и hourly freshness подтверждены отдельно | Пункт закрыт в пределах [акта](PDF_LESSONS_PRODUCTION_ACCEPTANCE_2026-10-15.md) и persisted scheduled receipt; не означает полный DR |
| PDF-бюджет | На всех пяти tenants healthy slots=2, Linux flock/crash/fail-closed и durable queue/leases проверены | 25/30 всей нагрузкой; real provider/delivery. Свежая crash lease ждёт до 300 с, secondary manual busy требует повтора |
| Ключи восстановления | Owner-аутентификация image-копии d0141b2/controls73 подтверждена, её offsite ciphertext прочитан и совпал по SHA | Независимая копия ключей вне рабочего ПК и восстановление без первой копии; отдельная физическая копия ключа не подтверждена |
| YooKassa | Автоматические/смоделированные проверки | Test-shop, настоящий тестовый платёж, частичный возврат, webhook/чек/пропорциональный срок |
| Ozon | Реализация и тесты | Непустые данные после отгрузки; отдельные Performance credentials для рекламы. Владелец отложил этап |
| Налоги | Реализация и тесты | Приёмка бухгалтером оснований/режимов/дат/НДС/зачётов/чеков |
| Mobile | Историческая Android/WebView проверка | Physical device, настоящий вход, production signing/store accounts; для iOS Mac/Xcode/Team ID; push отдельно |

Unknown старые архивы и Defender не переоткрываются. Ранее заблокированное удаление конкретных private temp folders не обходилось и secure erasure не объявляется. Проверенный migration tooling SQLite→PostgreSQL не означает выполненный production cutover.

## 6. Доказательства

Ниже локальные акты с техническими метаданными и SHA; секреты и финансовые значения в них не публиковались. Пути относительно каталога этой checkout.

- [Выкладка и pin-транзакция](../../deliveries/optimization-release-20261015/deployment-proof.json), [итоговый production gate](../../deliveries/optimization-release-20261015/final-acceptance-proof.json), [readback SHA](../../deliveries/optimization-release-20261015/proof-readback.json).
- Текущая d0141b2: [реальная copy-приёмка](../../deliveries/audit-followup-20261015/commission-copy-acceptance-proof.json), [выкладка](../../deliveries/audit-followup-20261015/commission-release-proof.json), [финальный live gate](../../deliveries/audit-followup-20261015/commission-live-final-proof.json), [tenant-api 685](../../deliveries/audit-followup-20261015/d014-tenant-api-full-receipt.json).
- Gateway33: [успешная выкладка](../../deliveries/audit-followup-20261015/compliance-mobile-review/release-proof-719e903bd399470d9cc2a132ed15cd69.json), [независимая проверка живого сайта, 12 mobile/desktop случаев](../../deliveries/audit-followup-20261015/compliance-mobile-review/live-719e903b/live-browser-proof.json), [gateway 676](../../deliveries/audit-followup-20261015/compliance-mobile-review/final/verification-receipt.json).
- [SMTP без отправки письма](../../deliveries/audit-followup-20261015/smtp-rotation-live-proof.json), [фактический cron/offsite](../../deliveries/audit-followup-20261015/scheduled-backup-readonly-proof.json).
- [Аутентификация images, load ещё открыт](../../deliveries/current-image-escrow-20261015/owner-proof-2616cd4e422549ae90b325b19a423633.json), [отдельный чистый VM boot](../../deliveries/local-dr-stand-20261015/evidence/clean-guest-boot.json).
- [Fresh тесты](../../deliveries/optimization-release-20261015/independent-test-acceptance.json), [576 сравнений](../../deliveries/optimization-release-20261015/independent-parity-proof.json), [сверка production-копий](../../deliveries/optimization-release-20261015/copied-financial-proof.json), [конкурентность копий](../../deliveries/optimization-release-20261015/copied-concurrency-proof.json).
- [Fault tests pin-транзакции](../../deliveries/optimization-release-20261015/pin-transaction-fault-proof.json), [отрицательная первая попытка](../../deliveries/optimization-release-20261015/operator-result-attempt1.json), [исправление manifest](../../deliveries/optimization-release-20261015/build-id-gate-correction.json).
- [Реальный image-load](../../deliveries/image-load-acceptance-20261015/image-load-owner-proof-01304705436e40e4a23df6e1e1953281.json), [независимая очистка](../../deliveries/image-load-acceptance-20261015/independent-final-cleanup.json).
- [Устранение world-write](../../deliveries/audit-reconcile-20261015/world-write-hardening-proof.json), [сверка объёма аудита](../../deliveries/audit-reconcile-20261015/report.md).
- [Историческая ночная приёмка](AUDIT_SHARED_HOST_NIGHT_2026-10-14.md), [исходный статус 13 октября](AUDIT_CURRENT_STATUS_2026-10-13.md).

## Сообщение Claude

Claude, актуальная база — `25ae99a3a148d041ed4a24d587bca6389cd858c3`: tenants d0141b2, gateway25ae99a, provisioner73c6a34. Ветка `claude/pdf-slots-observability-20261022` / `98e3d5f` прочитана; 141 focused tests прошли, прежние пропуски наблюдаемости исправлены. До слияния и20–30 прогона нужно закрыть воспроизведённые проблемы: отказ при чужом существующем томе ДО chown/seed; ошибку/UNKNOWN вместо ложного PASS при неудачном rm и недоступной проверке; admission и memory floor ДО создания каждого кабинета. Также обязательны lock dev/inode/mode, лимиты до/после, точное покрытие исходников и immutable overlay base. Подробный независимый воспроизводитель: `deliveries/audit-followup-20261015/claude-obs-review-af0b3e366f6f/`. Только scripts/tests/docs и локальные2–4; сохранить прежние положительные/отрицательные акты. Production stress, деплой, новые расходы и чужие сервисы не трогать. Лендинг582cdc3 пока не сливать с этой задачей; публичные юридические/инфраструктурные тексты требуют одобрения владельца. Fresh-OS DR остаётся у Codex; восстановление исторических образов уже проверено, полный запуск приложений после reboot ещё нет.
