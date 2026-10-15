# Приёмка PDF-бюджета и резервирования уроков — 15 октября 2026

Обе ветки Claude (`69e0fbe`, `eaecdba`) объединены с оптимизацией `89ad911`, проверены и выложены из **закоммиченного HEAD `73c6a34b8ee2940a25fe40ac8c65c2e06672c02d`**. Финальная приёмка начата `2026-10-15T00:16:59Z` (03:16:59 МСК), публикация backup cron — 03:19:28 МСК. Последующие коммиты документов не меняют эту production-ревизию.

## Проверенные образы и конфигурация

| Сервис | Immutable image |
|---|---|
| demo, mvvorokov | `sha256:0348ab4afa15b394534aed0d43a59a9899371a514e9d734ad36d9c718737af0b` |
| marketing | `sha256:93955f7d88034e2a313d368bf17c7ea8d907f86dc0df49732bb9a2922a19b354` |
| letoocean | `sha256:07fb39b4a04361edeeecbe4ea3dfecf9d08e9a5884ea997c5f33929ba11afe32` |
| alexdalex-msk | `sha256:4f57091355882ea2a0a89b2b7c83c6c7ae493ff37913a5d155175b5f3ededbc5` |
| gateway | `sha256:cbceb65e6bd6a904f68ed90f83fde86459dd483bf504a26c73286e4da672c203` |
| provisioner | `sha256:78d130e285adc8dfc916b4908b0e041209977995acc91d347cbaa358a2abcc24` |

Все семь сервисов работают с проверенной ревизией. У пяти tenants сохранены лимиты памяти, reservation, тома данных, aliases, restart policy, credentials/keyrings и frontend (285 файлов в четырёх кабинетах, 287 в marketing; отличается только ожидаемый build marker). Фактические UID/GID всех семи процессов — 10001/10001; исходная строка пользователя `appuser` у provisioner сохранена.

В каждом кабинете `/opt/wb-saas/pdf-slots` смонтирован как `/pdf-slots:rw`; diagnostics: `configured=true`, `healthy=true`, `host_slots=2`. Оба future pins и итоговая Compose-конфигурация проверены; gateway достигает `http://provisioner:8001/healthz`. Gateway изменяет только выделение новых slug, резервируя имена backup namespaces; существующие аккаунты не переименовывались.

## Проверки до и после выкладки

- Полные наборы: tenant-app **1270 passed / 1 skipped / 62 subtests**, tenant-api **685**, gateway **673 / 243 subtests**, provisioner **186**, scripts **355 / 3 skipped / 42 subtests**. App/API-файлы не менялись после их финальных прогонов. Последний runtime commit исправляет выбор образа только ежедневного course helper.
- Для всех пяти кабинетов сделаны свежие зашифрованные снимки. Candidate и rollback восстановлены без сети; **16 полных финансовых ответов** совпали по SHA. Это включает магазинный и помесячный P&L, по три SKU в demo/mvvorokov. Во всех пяти копиях 32 GET data-quality, четырьмя сериями по восемь параллельных, дали 200; проверены **505 экземпляров защищённых таблиц**.
- На Linux в собственных изолированных контейнерах шесть параллельных сборочных операций получили **максимум два слота**. После SIGKILL держателя слот освободился за **0,191 с**. Недоступный настроенный каталог блокирует новые сборки. Десять собственных test containers и временный каталог удалены.
- На production все пять публичных и подписанных health/business checks прошли. Финансы mvvorokov за 90 дней: **200, 6,110 с**, период подтверждён. Data-quality: 8/8 параллельных HTTP 200, максимум **0,734 с**. Это наблюдаемые ответы, а не доказательство холодного расчёта или неизменности live-таблиц: стабильность таблиц проверялась на копиях.
- В финальной проверке новых OOM и автоматических рестартов нет. Сохранность 16 опубликованных уроков и субтитров проверена.

Первый полный rollout остановился на немедленном публичном health после смены маршрута. **Все семь исходных ID/images были восстановлены**, rollback errors пусты, все пять публичных health ответили 200. Отрицательные акты сохранены. Добавлено ограниченное ожидание Docker-provider маршрута (~45–50 с), без принятия ошибок доступа, HTTP 500 или неверного JSON. 17 fault/readiness tests и независимая проверка прошли. Во втором rollout demo потребовал два повтора HTTP 404, затем ответил 200 за 4,254 с; остальные прошли сразу. Это подтверждает необходимое ожидание маршрута, не изменение приложения.

## Уроки: боевой backup, restore, S3 и расписание

Боевой архив `training-media-e677185580e2-20261015T000231Z-de8e7a.tar.gz.enc`: **97 144 598 байт**, SHA-256 `4a33d5a8108a719a80a4c3af80696aaa5c2f8b2f1a7632a85a633815af655ca9`. Использован существующий purpose-bound ключ, формат WBSAASBK2. Manifest: `e677185580e23431db0cf1f080851c43eab4f6578e06110723ed8d7591dcb74d`.

Все **32/32 MP4/VTT** восстановлены в пустой собственный каталог, совпали по SHA. Копия настоящего gateway проверила 16 video/source/subtitle tracks, 32 full GET, по 32 первых/средних/суффиксных Range 206 и 32 Range 416; все 16 VTT начинаются с WEBVTT. Источник остался неизменным по SHA, размеру и mtime. Production DB не монтировалась, сеть helper отключена, ключ передан через stdin без добавления в Config.Env. Удаление helper и plaintext work проверено **до** выдачи успешного акта.

Первый реальный прогон выявил отсутствие `cryptography` в gateway image. Исправление ежедневного pack использует точный образ работающего provisioner, где зависимость уже закреплена. Для отдельной restore-rehearsal сохранён gateway base с добавлением только crypto package из provisioner при одинаковых Python 3.12/cffi 2.1.1; это не изменение production gateway.

После обновления provisioner штатный узкий ежедневный helper прошёл за **6,050 с**, создал новую проверенную копию `training-media-e677185580e2-20261015T001754Z-74a631.tar.gz.enc`, SHA `eeed71722a7362b91a68c6496e61ff9d9af541c87e7fa822d0173a50dd2928c3`. ID production-контейнеров при этом не изменились; собственный helper удалён.

Группа training-media скопирована в S3 через существующее асимметричное offsite envelope, выполнен readback (`rclone check --download`), `offsite_verified=true`, без remote deletion. В `/etc/cron.d/wb-saas-offsite-backup` изменены только два пути на новый committed runner; существующие расписания, аргументы и получатель alert сохранены. Суточная копия включает уроки до offsite; hourly watchdog требует свежесть группы. Реальный `check`: **fresh=true, error=null, exit 0**.

Новые ежедневные архивы уникальны. Retention удаляет только свои старые копии после аутентификации и проверки имени/manifest/inode; чужие, неизвестные и непроверенные архивы сохраняются. Первое срабатывание нового cron по часам ещё не наблюдалось; функции daily helper и watchdog исполнены вручную в штатном окружении.

## Оставшиеся границы

Это не приёмка одновременной нагрузки 25/30 кабинетов и не полное восстановление на новой ОС. Исторический image-load проверяет старый escrow исходных образов, а не escrow новых образов семи сервисов (шесть уникальных image ID). Свежая аренда прерванного PDF может ждать до 300 секунд; слот ОС освобождает сразу. Ручной PDF дополнительного магазина при busy требует повтора; его resident-очередь не добавлена. Реальная доставка, WB/Ozon sync и provider writes этой проверкой не принимаются.

`key_escrow_confirmed=false` сохраняется: независимая копия ключей вне первого устройства остаётся задачей владельца. [Статус offsite](../../deliveries/pdf-lessons-release-20261015/proofs/training-offsite-status.json) также содержит `alert=failed` для этого предупреждения: доставка такого уведомления здесь не подтверждается. Проверка свежести прошла без необходимости alert. YooKassa test-shop, Ozon после отгрузки, бухгалтерская приёмка налогов, physical mobile/signing/stores и новый OS/boot/DNS/TLS остаются отдельными gates.

## Акты

[Production и finance](../../deliveries/pdf-lessons-release-20261015/proofs/final-acceptance-proof.json), [выкладка, snapshots и rollback](../../deliveries/pdf-lessons-release-20261015/proofs/deployment-proof.json), [Linux слоты](../../deliveries/pdf-lessons-release-20261015/proofs/linux-pdf-gate.json), [боевое восстановление уроков](../../deliveries/pdf-lessons-release-20261015/proofs/training-production-proof.json), [ежедневный helper](../../deliveries/pdf-lessons-release-20261015/proofs/daily-training-gate.json), [offsite](../../deliveries/pdf-lessons-release-20261015/proofs/training-offsite-proof.json), [cron](../../deliveries/pdf-lessons-release-20261015/proofs/cron-publication-proof.json), [watchdog](../../deliveries/pdf-lessons-release-20261015/proofs/backup-freshness-health.json), [SHA актов](../../deliveries/pdf-lessons-release-20261015/proofs/artifact-sha256.json), [финальные операторы](../../deliveries/pdf-lessons-release-20261015/proofs/final-operator-sha256.json). Секреты, PIN и финансовые значения в эти акты не переносились.
