# Полный рабочий контекст магазина и распределяемый FIFO-склад

База: c37c6aa. Изолированная ветка: codex/audit-shop-operations-20260930. Продакшен не изменять из этой ветки. Это продолжение P2 полного аудита, а не переименование существующего финансового snapshot в «полноценный магазин».

## Фактический исходный контракт

Аудит FULL_PRODUCT_MARKET_AUDIT_2026-09-23.md: строка 109 требует переключатель, консолидацию и shop_id в учёте/правах; строка 185 требует консолидацию по источникам и запрет двойной продажи одного остатка. Сейчас shop_workspace_worker.py поддерживает только sync/cost/report. Результат — financial/products/stocks; он не обслуживает основные рабочие экраны и документы. Child process с отдельным DATA_DIR и SQLite snapshot нужен для изоляции: менять DB_PATH глобально в API нельзя.

Основной склад уже имеет finished_goods_cost_layers, variant chrt_id, карантин held_units, finished_goods_fifo_allocations, производство producer_release_id и проверку отмены последующих этапов. Новая модель обязана опираться на эти слои и историю. _move_finished_goods_fifo сейчас вычитает только карантин; резервы любого магазина должны уменьшать доступность также для всех прежних потребителей, отмен и перераспределений.

## Зависимости и порядок реализации

1. **Контекст и права**: устойчивые shop identities + ACL, request-scoped проверка, child dispatcher, матрица scope всех API. Без этого нельзя открывать полноценные secondary операции или выбирать credentials.
2. **Канонический склад**: explicit SKU/variant mappings, общий allocator и immutable документы, защита прежних FIFO-потребителей. Без этого нельзя подтверждать зарезервированный заказ, отгрузку или продажу магазина.
3. **Связанный жизненный цикл**: подготовленный shop snapshot + canonical warehouse transition сохраняются одной транзакцией main SQLite с optimistic revision. Только после этого включить изменения shop-bound документов.
4. **UI полного контекста**: общий переключатель, abort устаревших загрузок, идентичность в формах/preview, все data sources принимают выбранный контекст. Ошибка secondary никогда не подменяется primary/mock.
5. **Сверка и приёмка**: реальные fixture snapshots двух продавцов с совпадающими nm_id; конкурентные резервы и продажи; корректная стоимость, возвраты, отмены, права; штатный encrypted backup/restore; exact-memory runtime smoke; deployment интегратором.

Эти этапы зависят друг от друга. Отдельно выкатить «полноценный переключатель» до складского/документного контракта нельзя: он будет направлять часть операций в чужой контекст или расходовать физический запас дважды. На промежуточных стадиях неподдерживаемые операции явно блокируются, но аудит не считается закрытым.

## Контекст API и UI

- Primary shop 0: прежняя база и данные сохраняются, credentials не копируются в дополнительные магазины.
- Secondary shop N: immutable account_identity с проверенным seller sid, encrypted token, отдельный snapshot/revision. Отключённый, неизвестный или недоступный N → явная ошибка, без fallback в 0.
- Единый registry verified seller identity включает primary 0. Две ротации token одного sid не образуют два shops; изменение primary seller identity не перепривязывает старые данные молча и не допускает дубликат уже подключённого secondary. Проверка действует на create/reconnect/primary token replacement, а не только на listing.
- Общий склад и производство: отдельный явно названный tenant resource. Shop context в заказе/производственном назначении ссылается на целевой магазин, но не создаёт второй физический склад.
- Tenant-global ресурсы: пользователи, настройки доступа, backup, платёжные аккаунты/деньги, общие уведомления. В UI явно указано, что они общие; не выдавать их за сведения выбранного магазина.
- Read-only seller client в child использует только selected token. По умолчанию запретить WB внешние изменения: цены, карточки, ответы, возвраты, отгрузки/поставки/печать с изменением состояния. Существующие primary внешние маршруты не должны запускаться в secondary контексте случайно.
- Shop-bound страницы: Сегодня, товары/размеры, аналитика и финансы/FIFO/export, реклама (чтение), клиентское качество (чтение), заказы/поставки, задания/контроль/инциденты, локальные закупочные документы и отчёты. Производство/материалы/мощности/спецификации доступны как общий канонический контур с явным target shop.
- Сначала составить machine-readable route scope registry для каждого existing GET/write, включая routers. Неизвестный маршрут с shop context отклоняется; несовпадающие URL/header/body shop IDs отклоняются. Роль проверяется против исходного business path до dispatcher. Worker получает только проверенные actor/role/shop, не произвольный path или headers.
- Owner управляет shop access. Доступ участников ограничивается списком разрешённых shops; новая shop ACL миграция не расширяет прежние права автоматически. Owner имеет все shops; для прежних secondary чтений/записей команда получает совместимость лишь по явной миграционной политике, которую надо утвердить перед кодом. Не выдавать интеграционной роли полный dashboard/склад.
- Переключение замораживает/закрывает старый draft либо требует явного переноса, сбрасывает replay key/preview/confirmation, отменяет предыдущие запросы. Ответ с другим shop identity/revision игнорируется. Во всех подтверждениях указан магазин/общий склад.

## Каноническая идентичность склада

- `warehouse_items`: внутренний UUID + явно выбранный backing (nm_id,chrt_id) существующего производственного FIFO, название, revision. Это физический производственный SKU; nm_id здесь не означает автоматическое равенство карточек двух продавцов.
- `shop_sku_mappings`: shop identity + marketplace + external nm_id + exact chrt_id → warehouse_item UUID; unique exact source identity, подтверждённая пользователем связь и версия. Нет merge по совпадению nm_id, артикулу или названию. Вариант 0 не заменяет конкретный размер.
- Новый secondary товар без производственного соответствия остаётся unmapped и не получает доступного общего запаса. Для собственного canonical SKU сначала явная регистрация производственного товара/варианта в существующем учёте, затем mapping. Исторические отдельные secondary FIFO оценки не сливать в shared inventory.
- `warehouse_reservations`: UUID/request_key, shop_id+stable identity, source document identity, mapping revision, quantity, due_date, status, revision, actor, timestamps, immutable original payload.
- `warehouse_reservation_layers`: reservation → точные existing layer IDs, variant, location, quantity, cost/provenance snapshot. FIFO chronology ограничивает слои датой документа; карантин и активные чужие резервы вычитаются. Резерв не меняет физическое количество и не признаёт расход.
- `warehouse_documents` + append-only events: reserve/commit/release/receipt/sale/return/cancel, source document+shop identity+operation kind unique; idempotent exact replay, changed payload conflict; verified actor, UTC; UPDATE/DELETE/INSERT OR REPLACE guards.
- Все переходы выполняются одним main SQLite BEGIN IMMEDIATE. Доступное количество = существующий физический FIFO минус карантин минус активные резервы; preview ничего не распределяет.
- SQL guard удерживает holds + foreign active reservations ≤ physical quantity. При commit собственный reservation allocation переводится в состояние физического движения внутри той же BEGIN IMMEDIATE до уменьшения ready; никакой чужой резерв не снимается. Ошибка/rollback восстанавливает и reservation state, и physical balances. Нельзя временно нарушать invariant и надеяться восстановить его следующей транзакцией.

## Заказ → резерв → производство/приход → продажа/возврат

1. Заказ selected shop появляется из verified WB read или явного локального документа. Уникальность включает shop identity и source order ID. Отсутствующий mapping явно блокирует физический резерв.
2. Preview потребности показывает свободный остаток, точные варианты, нехватку, будущую потребность производства и даты. Confirm резервирует только существующие допустимые слои. Нельзя резервировать будущую партию задним числом. Заказ с нехваткой остаётся awaiting_stock с явным статусом; не создаётся синтетический физический слой.
3. Выпуск/подтверждённый приход создаёт существующий FIFO-layer с immutable стоимостью/producer provenance. После появления партии оператор подтверждает дополнение резерва; не переписывает исходную спецификацию/нормы задним числом.
4. Commit отгрузки использует только принадлежащие reservation слои, существующие FIFO allocations и связанные movement IDs; меняет ownership/location и status атомарно. Физическое количество нельзя одновременно оставить свободным в общей ready зоне и включить в количество магазина.
5. Продажа списывает только принадлежащую shop распределённую единицу с точной mapping/version/source-sale identity. Child sync не должен одновременно повторно списать эту же центральную единицу или досоздать её из primary WB snapshot. Для unmapped исторических продаж показывается отдельная оценка без притязания на точное shared FIFO.
6. Возврат с подтверждённой source-sale linkage возвращает те же исходные слои/стоимость в указанную карантинную/доступную location. Возврат без продажи/варианта/количества отклоняется либо остаётся нерешённым incident. Повторный return ID не возвращает количество повторно.
7. Отмена резерва освобождает только неиспользованную часть. Отмена отгрузки невозможна после продажи, возврата/последующего движения или чужого резерва на возвращаемой части без правильного обратного порядка. История не удаляется. Межмагазинное перераспределение — явный новый документ, а не исправление shop_id старой продажи.

## Транзакционный мост snapshot ↔ общий склад

Worker готовит новый SQLite snapshot и business result, но не сохраняет его самостоятельно для warehouse-bound transition. Parent проверяет текущую active shop identity/revision, mapping revision, source document/replay и FIFO availability, затем в одной транзакции записывает snapshot blob, центральные movements/reservations/events и receipt. Если worker падает, main write не происходит; если parent conflict, prepared snapshot отбрасывается. Нельзя подтверждать сначала склад, затем отдельно store_snapshot: это создаёт разрыв после crash.

Read-only child API adapter переиспользует существующие API/расчёты в отдельном process DATA_DIR и возвращает строгий bounded JSON/PDF/CSV envelope. Он не принимает произвольные FS paths, subprocess commands или forwarded internal environment. Вызов authenticated original path проходит role/scope checks также в worker; local-only mode блокирует внешние WB writes и отправку уведомлений. Кэш и response correlation всегда включают shop identity/revision/period.

## Ключевые проверки до закрытия блока

- Два shops с одинаковым nm_id, разными sid/названиями/себестоимостью: все страницы/exports/local writes остаются в выбранном shop. Чужой mapping не используется, primary credentials не вызываются. Недоступный child → ясная ошибка.
- Конкурентные reserves двух магазинов при ready=3: 2+2 не подтверждаются оба; available никогда не отрицательный. Primary manufacturing, dispatch, incident loss, reversal и sync не обходят резерв.
- Exact variants, разные партии/даты/цены, nested producer provenance: себестоимость sale/return совпадает с исходными allocations, без новой текущей оценки.
- Запись shipment/sale/return и snapshot atomic; crash-before/after commit, stale revision, replay with changed source/payload, transport failures и rollback проверены.
- Нет двойного расхода между primary FIFO sync, secondary sync и allocator; нет автоматического physical stock merge по совпадению WB IDs.
- Owner/manager/shop ACL + производственные роли + integration_reader; CSRF; inactive shop; unknown shop; mismatch request/body identity; невозможно вызвать произвольный worker route.
- UI переключение во время сетевого запроса/preview/write не переносит результат или replay key в другой магазин. Shared resources подписаны явно.
- Native encrypted backup/restore сохраняет все shop blobs, stable identities, exact mappings, reservations/layers, документы/events и replay receipts. Downgrade к pre-allocator image после новых документов требует восстановления согласованного снимка, иначе старые FIFO-потребители обходят резервы.
- Full tenant/API/UI/build, SQL guards, exact 1.5GiB private clone stress + staged smoke + read-only live identity acceptance. До этого блок не объявлять закрытым.

## Решения для независимого архитектурного review до реализации

1. ACL миграция прежних участников: owner-only для secondary по умолчанию либо сохранение existing team доступа с явным seeded ACL. Требуется выбрать совместимую политику, не изменить права молча.
2. Каноническое производство остаётся одним tenant ресурсом; создание отдельного manufacturing snapshot на магазин запрещено, если товар использует shared warehouse. Уточнить UI target-shop назначение и мастер-каталог.
3. Точные shared FIFO продажи должны проходить единый main transition; secondary финансовый snapshot получает производную immutable cost provenance для своих financial rows. Нельзя оставить независимые FIFO списания worker рядом с central allocator.
   Фактический риск: fifo_sales.process_sales_fifo_events ловит любой ValueError, кроме COST_PENDING, и создаёт synthetic_sale слой. Shared allocation/ownership/reservation errors обязаны иметь typed fail-closed код и не проходить эту ветку; иначе запрет резерва будет обойдён новым синтетическим слоем. Возврат без точного совпадения также создаёт return_unmatched; для mapped shared SKU такой слой не должен увеличивать распределяемый физический запас. Финансовый факт WB остаётся видимым с неполным cost basis, а физическая операция ждёт решения.
4. Полный route registry велик; можно параллелить UI контекст и worker adapter только после фиксации schemas/envelope/scope. Warehouse lifecycle код имеет приоритет над объявлением всех secondary кнопок рабочими.
