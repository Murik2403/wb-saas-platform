# Построчная сверка требований аудита — 30 сентября 2026

**Весь аудит не закрыт.** Проверка сводит исходные требования, выпущенный код, приёмку и внешние зависимости. Это независимая сверка документов и исходников, не новый прогон всех тестов и не удостоверение полноты клиентского учёта.

## Источники и правило статуса

- `F`: `../wb-saas-platform-clean/docs/marketing/FULL_PRODUCT_MARKET_AUDIT_2026-09-23.md`, исходный широкий аудит; номера строк ниже относятся к этому файлу.
- `C`: `../wb-saas-platform-clean/docs/marketing/PRODUCT_AUDIT_2026-09-23.md`, аудит Claude, разделы 0–5. Его рыночные утверждения не становятся программными обязательствами и не публикуются как доказанное превосходство.
- `M`: `AUDIT_CLOSURE_MATRIX_2026-09-30.md`; `G`: `AUDIT_CANDIDATE_GATE_2026-09-30_32cb605.md`.
- `P`: `../../../release-rehearsal-tools/PROOF_prebuilt_image_32cb605.json` — фактические 222 server / 9 browser файлов; `L`: `PROOF_independent_live_pin_32cb605_20260930.json` — независимое чтение работающего exact image, healthy, restart 0 и pin provisioner; `R`: `PROOF_tmpfs_rehearsal_32cb605_20260930.json` — изолированное восстановление, 14 candidate / 5 rollback API, ручной cash/Ozon сценарий. `R.production_deployed=false` относится к репетиции, а не к последующему выпуску.
- Исторические `PRODUCT_AUDIT_IMPLEMENTATION_2026-09-23.md`, `PRODUCT_AUDIT_RELEASE_STATUS_2026-09-25.md`, `AUDIT_TRANCHE_ACCEPTANCE_2026-09-30.md` полезны как журнал; прежние «не реализовано» не заменяют более поздний код/релиз.

Статусы: **В** — реализовано и входит в подтверждённый выпуск; **П** — реализовано, конкретная приёмка ещё открыта; **К** — отсутствует требуемый программный кусок; **Д** — реальная проверка/публикация требует внешних данных или действия; **У** — явно условная/отложенная рекомендация, не обязательство копировать конкурента. Составной статус означает частичное закрытие, а не «всё готово».

Текущий tenant: `e217f77cca2dc57c03a6fda55019a061acd5fe0e`, image `sha256:fb4f9d224a756ab30d05834b0838653899f8743b881cdc832cf837ac1b745b65`; Gateway: `8221f2975fd0b7f110b7871131a992dd35c27377`, image `sha256:9a3d4dfcdba226ce21ad9e47cff6d49f95664a27cbbb9f95922e58bdb451355d`. Старые G/L/R остаются историческими доказательствами. Независимые новые proof вне Git: `../../../release-rehearsal-tools/PROOF_independent_live_pin_e217f77_20260930.json` и `PROOF_independent_gateway_8221f29_20260930.json`: actual image, healthy, restart0, OOMfalse, provisioner `0eb4746` с точным pin fb4f9d; health/index/новый `index-C61Ha8Em.js` — 200. Сохранённый container label tenant отражает старый base `09a43baf`; source revision проверен через label exact image. Image build marker `/tmp/redesign-build-id` скрыт live tmpfs, поэтому его наличие в image не объявлено чтением live marker.

Новый root byteproof `../../../audit-private-deliverables-20260930/PROOF_prebuilt_image_e217f77.json` подтверждает 224 runtime и 9 assets, manifest `f4d6d0c67acc34fbe356231dcced0de1ba28f503dc387ba43f969e1d9d28ec32`. По свежему release report root: 652 DB passed/1 skipped/8 subtests, 413 API, 232 frontend, 403 Gateway passed/2 skipped/186 subtests; native17 procurement и 5 composed controls; свежий encrypted backup restore gate, 17 live API и HTML/assets. Независимо повторены 55 focused DB +14 HTTP на exact e217f77. Rollback tenant: `wb-tenant-mvvorokov-rollback-20260930-201839`. Общий dirty main не изменялся; клиентские записи reviewer не менял.

## Финансы и обязательные дефекты F §4

| ID / требование | Статус | Доказательство / конкретная граница |
|---|---|---|
| F P0-1: per-unit только пересечение дат | В | `tenant-api/api_server.py`, `tenant-app/db/manual_expenses.py`; M периодизация; G сквозной управленческий сценарий. Месячное окно не умножает дневной расход на все продажи. |
| F P0-1: revenue-percent только пересечение | В | Те же источники и G. Возвраты и корректировки должны оставаться подписанными операциями, не абсолютными суммами. |
| F P0-2: единый исторический COGS | В / Д | `db/historical_cogs.py`, `calculations.py`, `db/fifo_sales.py`; G/R. Точная история не меняется от новой текущей ставки; у клиента без документов остаётся оценка. |
| F P0-2: продажи, возвраты, варианты, покрытие | В / Д | `db/wb_fifo_reconciliation.py`, `db/finished_history_import.py`, `tests/test_finished_history_import.py`. Ручной исторический импорт помечен предварительным и не доказывает точный WB FIFO. |
| F P0-3: единый управленческий результат | В | M; `api_server.py`, `db/manual_expenses.py`, `db/store_expense_workflows.py`; G сквозные суммы. WB-сверка раскрывается отдельно, внешние расходы не второй независимый итог. |
| F P0-3: источник/документ, внешний расход, замена/сверка | В | Реестр источника, включение и документальная дедупликация, тесты manual/store expenses; не приравнивать произвольную строку файла к доказанному API-документу. |
| F P0-3: подтверждённый ноль / N/A / оценка / неизвестно | В / Д | `db/expense_coverage.py`, `ExpenseCoveragePanel.tsx`, тесты; M релиз95b6ed8. Реальный owner сам подтверждает факт, 0/9 не заполняется агентом. |
| F P0-4: убрать неявную упаковку12 | В | `db/production.py:703`, `settings_history.py:12`, `confirmed_packaging_cost`; M/G. Нулевое подтверждение отделено от неизвестного. |
| F P0-5: калькулятор и FAQ/JSON-LD согласованы | В | `gateway/templates/marketing_calculator_page.html`, `marketing_cost_faq.html`, G Gatewayc9b0ef9; фактическая формула остаётся сценарием по вводам. |
| F P0-6: текущие рекламные URL/UTM/conversion | Д | Кампания передана Claude владельцем. Код/HTTP200 сайта не доказывает действующие объявления и signup; ссылку нежелательного сравнения не восстанавливать. |

## Продукт F §5 и дополнительные требования C §3–4

| ID / требование | Статус | Доказательство / оставшаяся работа |
|---|---|---|
| F101 / C: единицы кг/л/м/шт в UI | В | `db/production.py:106`, `ManufacturingCosts.tsx`; `tests/test_material_unit_generalization.py`. Базовая единица хранится на материале; рулон→базовая единица поддерживается отдельно. |
| F101: конверсия единиц и контроль размерности | В | Выпущены `74fdb25`, `59dcc5a`, `e217f77`: общий Decimal adapter `db/material_units.py` для BOM, opening CSV и закупочной приёмки; г/кг, мл/л, мм/см/м внутри размерности. Сохранены исходный ввод/коэффициент/базовое количество и цена, immutable history, CSV fingerprint/replay. Межразмерностные преобразования и неподтверждённая плотность/упаковка запрещены; доля копейки и потеря точности отклоняются. Native tests закрывают CNY, partial receipt, undo, WIP и поздний rollback без ложных счетчиков. |
| F102 / C BOM: несколько компонентов | В | `db/manufacturing.py:283`, `ManufacturingCosts.tsx`, `tests/test_manufacturing.py`; G материал/работа/упаковка. |
| C BOM: размер/цвет, неизменяемая версия | В | `save_bom_version(...chrt_id)`, `tests/test_wip_variant_identity.py`; стоимость прежнего выпуска сохранена. Старое замечание25.09 о missing variant WIP устарело. |
| C: многоуровневые полуфабрикаты | В | `manufacturing.py:168,220`, `shared_manufacturing.py`; G/R; версия вложенного BOM, FIFO и порядок отмены. Это не только legacy НЗП. |
| F103 / C: НЗП React, выдача/возврат/частичный выпуск | В | `db/wip.py`, `ManufacturingCosts.tsx`, `tests/test_production_wip_variant_ui.py`, M/G. |
| F103 / C: брак/потери конкретной партии | В | `manufacturing.py:600`, `IncidentWorkflow.tsx`, `tests/test_manufacturing.py`; резерв/карантин общего склада ограничивает доступный остаток. |
| C: сдельная работа по сотруднику/операции/партии | В / Д | `manufacturing.py:377`, `ManufacturingCosts.tsx`; стоимость выпуска сохраняется. Это не кадровый учёт, зарплатный платёж или налог; реальная оплата отдельно в ДДС. |
| C: износ оборудования в себестоимости | В / Д | `manufacturing.py:252,353`, тесты; ограничение амортизируемой стоимости. Это управленческий норматив ресурса, не бухгалтерская амортизация. |
| F104: общие расходы и распределение | В | `manual_expenses.py:108`, `ManualStoreExpenseManager.tsx`, M; общие расходы не считаются отдельными денежными поступлениями. |
| F104: повторяющиеся статьи | В | `store_expense_workflows.py:43,85`; явное включение, уникальная генерация периода. |
| F105: импорт каталога/материалов/начального сырья | В | `db/migration_import.py`, `MigrationImport.tsx`, тесты; mapping→preview→confirm, журнал, replay. Это не импорт любого произвольного формата без шаблона. |
| F105: начальные готовые партии и история | В / П / Д | `db/finished_history_import.py`; fresh exact-image CUA proof `PROOF_csv_inline_history_e217f77_20260930.json`: unchecked inline consent, disabled confirm до проверки; opening3units/1.01 imported once, repeat preview blocked; historical10original/5remaining/50 и sale10/return5 — 2 документа/2 allocations, без повторного физического списания, DB integrity/FK ok. Network-unknown UUID replay покрыт API, не принят в браузере. Реальных документов владельца нет; архивные возвраты после cutover не поддерживаются данным импортом. |
| F105: импорт→общий склад без двойной стоимости | В | `imported_shared_basis.py:63,68`, G; actual browser6units/2.02, два варианта зарегистрированы, shop mappings явно отдельно. |
| F106: рекурсивная потребность/остатки/резерв/сроки | В | `db/supply_planning.py`, `tests/test_nested_supply_planning.py`; M/G. Нельзя считать выведенный заказ письмом поставщику. |
| F106: редактируемый preview→внутренний заказ | В | supply planning API/UI; подтверждение создаёт внутренний документ, повтор защищён. |
| F107 / C: история автор/причина/до/после | В | `db/settings_history.py:33`, `tests/test_settings_history.py`; прежние события до включения журнала не восстанавливаются. |
| F107 / C: права по операциям и роли цеха | В | API allowlist technologist/cutter/seamstress/warehouse; M/G и tests; не только скрытые кнопки. |
| C: API интегратора | В | integration_reader, три ограниченных GET маршрута каталога/материалов/выпусков; scoped key. Это не полный публичный write API продукта. |
| F108: план/факт ДДС, обязательства, прогноз | В / Д | `db/cashflow.py`, `CashflowPanel.tsx`, M/G/R. Клиент вводит действительные даты/начальные остатки, продажи не равны банковскому приходу. |
| F108: счета, валюты, парные переводы | В | `db/cash_accounts.py`, `CashAccountsPanel.tsx`, R; четыре валюты, отдельный остаток, атомарная отмена перевода. |
| F108: выписка preview/confirm/dedup/link | В / Д | `db/cash_statement.py`, `CASH_ACCOUNTS_ACCEPTANCE_2026-09-30.md`, R. Канонический CSV; прямые банковские API и адаптер каждого банка не заявлены. |
| F109: переключатель, отдельные магазины, права | В | `ShopWorkspaces.tsx`, `ShopContextBoundary.tsx`, M; дополнительные WB-магазины изолированы. |
| F109: консолидация и общий остаток | В / Д | `db/shared_warehouse.py`, `SharedWarehouseFinance.tsx`, G; explicit mappings/разрешения, резерв→отправка→приёмка→sale→return/quarantine. Реальное сопоставление клиента отдельно. |
| F109: расписания нескольких магазинов | В / Д | `db/shop_schedules.py`, `SecondaryShopSchedules.tsx`, `SECONDARY_SCHEDULES_ACCEPTANCE_2026-09-30.md`; фактический killparent+2PDF приняты. Доставка/расписание клиента выключены по умолчанию. |
| F110: Ozon товары/остатки/заказы/возвраты rawAPI | В, ограниченно | `db/ozon_accounting.py`, `OzonAccounting.tsx`; сбор версии источника, лимиты/пагинация/failclosed. Дополнительные WB-shop scopes не могут обращаться к чужому Ozon. |
| F110: Ozon ручной денежный журнал/FIFO | В / Д | `db/ozon_pnl.py:194,209,226,251`, `OzonHistoricalPnl.tsx`, R; sale-return-resale, dedup, unknownNULL, физический WB неизменен. Достоверность ручных документов подтверждает владелец. |
| F110: автоматическое API→сверенный денежный журнал | **К / Д** | `ozon_pnl.py:270` явно `api_normalization_verified=False`, `marketplace_source_complete=False`; нет полного автоматического posting/item/nonitem/container нормализатора. Нужны достоверный актуальный контракт и непустой пример. Пустой ответ не доказательство нулевой прибыли. |
| F111 / C: календарь мощности | В | `db/production.py:215,252`, UI; планы сохраняются, это не автоматическое оптимальное расписание. |
| F111: рабочие центры/нормы/перегрузка/перенос | В / Д | `db/workcenters.py:43,65,99,113`, M; истинные мощности и времена операций клиента требуют документов. |
| F112: доказательства/ответственный/срок/история | В | `db/incident_workflow.py:39,55`, `IncidentWorkflow.tsx`, tests. Файл/локальный outcome не доказывает принятие претензии WB. |
| F112: карантин доступных FIFO-слоёв | В | `incident_workflow.py:79–98`, `shared_warehouse.py:600`, G; зарезервированное количество не доступно повторной продаже. |
| F113: маркировка/сканер code→order→return | У / Д | Исходное требование прямо «по спросу целевых категорий». Нужны категория, реальные коды, устройство и workflow; универсальный регуляторный процесс не реализован и не обещается. |
| F114: цена/безубыточность/закупка what-if | В | M, сценарии API/UI; расчёт по предположениям, без изменения WB цен. Возвраты и новая партия влияют на сценарий, не переписывают фактический отчёт. |
| F116: ABC уже есть; интерактивный ABC/XYZ | В / У | `tenant-app/reports` содержит ABC; исходник предлагает фильтр как отдельный вопрос развития, не обязательную реализацию отсутствующего ABC. |
| C CSV финансов | В | App.tsx:1841, UTF8 BOM/защита формул, finance export tests/G. Исходное «CSV нигде нет» было слишком широким. |
| C ложная ChatGPT/Claude надпись | В | App.tsx:3156,3162 «Агенты цены и рекламы», входят в проверенный runtimeP. Правила не выдаются за LLM. |
| C роли/guardFIFO как позиционирование | В / У | Главная/capabilities описывают свои функции; запрещённые владельцем сравнения не возвращены. Проверяемое собственное описание не подтверждает рыночное лидерство. |

## Сайт, доказательства и развитие F §7–9 / C §5

| Требование | Статус | Доказательство / остаток |
|---|---|---|
| F84–86,162: датированное демо 5 действий | В / П | M и AUDIT_TRANCHE: actual refresh30.09success, fivecontrols true, freshdate, workerenabled, изоляция клиентских баз. Публичный `/demo` visual acceptance открытый; следующий календарный запуск ещё не наблюдался. |
| F163: карта экранов/источников/ввода | В | `gateway/templates/capabilities.html` исправлен и live-проверен в Gateway8221f29: cash canonical CSV, общий склад с явными mappings, ручной Ozon FIFO; нет обещаний прямого bankAPI, автоматических shop mappings или полного Ozon API normalization. `/llms.txt` согласован. |
| F164: calculator→account→savedSKU | В | M/Gatewayc9; owner-only, chosenSKU servercheck, сохранение/повторное открытие в реальном браузере; вымышленные суммы не фактические затраты. |
| F165: мастер старта, примеры/ошибки/продолжение | В / П | M и MigrationImport; первый полезный результат отдельный от WBconnection. Полный CSVconfirmUI остаётся открытым. «5минут» не обещается. |
| F166: обезличенный реальный кейс | Д | Приватный черновик по read-only периоду1–29сентября подготовлен; exactFIFO0/unknown1 и другие ограничения сохранены. Нет исходных исторических документов и разрешения на конкретную публичную версию; не клиентский отзыв. |
| F167: обзор2–3мин актуального интерфейса | В (материал) / Д (публикация) | M: approvedMP4 150s1920×1080/30fps, sixscenes, decode0errors, syntheticlabels. Ролик создан локально, публикация не выполнена, звука нет. |
| F167: инструкции по задачам; новое требование11уроков | П | `TRAINING_LESSON_INVENTORY_2026-09-30.md` — план, не готовые11видео. Каждому уроку нужны actualUI действия, числа, права, корректный источник и просмотр готового материала. |
| F168: тариф магазины/участники/история/cancel | В | `gateway/templates/pricing.html`: main+5WB,128MiB,начальная7–90/history365/Ozon31, отмена; неопределённый userlimit не назван unlimited. |
| F168: помощь переноса | П | pricing даёт contacts/support, capabilities preview. Точная оказываемая услуга и срок помощи не подтверждены контрактом; не обещать SLA или бесплатную полную миграцию без основания. |
| F169: токены/выгрузка/восстановление | В | capabilities, encryptednativebackup/G/R/L. Проверено изолированное восстановление, не абсолютная безопасность всех процедур. |
| F170 / F88–90: favicon/wwwTLS/title | В | M/G publicroute200/canonicalTLS без отключения сертификата; faviconstaticSVG. Новый liveSEOscan не выполнялся этой сверкой. |
| F170: журнал обновлений/поддержка | В / П | Gateway8221f29: публичный `/updates` с датой и уже выпущенными возможностями; фактически отрендерен root CUA, независимо подтверждены 200/sitemap/llms/footer. Новые unreleased возможности в журнал не приписаны. Contacts/support есть; delivery emails/Telegram не объявлены проверенными без реального отправления. |
| F170: statuspage с настоящим мониторингом | У | Исходник «имеет смысл, если связана с реальным мониторингом», не обязательство добавить статическую зелёную страницу. |
| F172: новыеSEO под отдельный спрос | У / Д | Старые частотности не свежий срез; нужны актуальные выгрузки. Владелец передал рекламу Claude. Непроверенные конкурентные сравнения не публиковать. |
| F199:5–10реальных интервью | Д | Не выполнены. Вопросник не интервью; нужны адресаты/канал и явное разрешение на сообщения. Отсутствие контактов не исправить программно. |
| F201: activation/result/time/D7/payment/churn | В (сбор) / Д (наблюдение) | M:0realclients/2excluded, ratesNULL; служебная регистрация помеченаTest. Цели/LTV/CAC не выдуманы. Нужна реальная когорта и время наблюдения. |
| C§5: мобильная адаптивность всех экранов | В / П | Fresh e217f77 `PROOF_mobile_sections_e217f77_20260930.json`: actual CUA navigation/headings всех11разделов при390×844, document width375, без horizontal overflow на sparse synthetic fixture. Заполненные клиентские таблицы/бизнес-действия/все устройства этим не приняты. |
| Доступность:dialogfocus/Tab/Escape/return | В / П | `dialog-focus.ts`; `PROOF_csv_keyboard_ui_32cb605_20260930.json`: actual Tab/Shift+Tab wrap, Escape и return focus приняты для cost drawer и task drawer без записи. Другие dialogs и screen-reader speech не считаются автоматически принятыми. |
| Доступность:речь screenreader | П | Не проверено. ARIA/rolealert и видимыйfocus не доказывают произнесённые labels/errors. |
| C§5: отзывы конкурентов/актуальные тарифы/неиндексируемые аналоги | У / Д | Исследовательская неопределённость, не задача разработки; проверки нужны перед внешними сравнительными заявлениями. Старые рыночные цифры не объявлены свежими30.09. |
| F205 / C «не делать»: внешниенищи/РРЦ/контрафакт/plugin | У | Исключено исходными рекомендациями; отсутствие этих модулей не мешает закрыть согласованный производственный scope. |
| F205: storefront/cards/fullbookkeeping/autonomousads | У | Не рекомендовано в ближайшем цикле; не включать в аудит как безусловный deliverable. |

## Обязательные контрольные сценарии F189–195

| Сценарий | Подтверждение | Что не считать доказанным |
|---|---|---|
| Два прихода→выпуск→границаFIFO→возврат | G exactLinux и R ручнойOzon; сохраняемые manufacturing/FIFO tests | Отсутствующие клиентские документы и исторические оценочные слои не стали точнымFIFO. |
| Дневной расход в месяце, percent/per-unit | G сквозной management; исходные regressiontests | Полнота всех9статей владельца не подтверждена автоматически. |
| API и файл одной статьи не дубль | expense workflow/document idempotency и R statement linking | Произвольный OzonrawAPI пока не автоматически денежный документ. |
| Ткань/клей/упаковка, новыйBOM не меняет старое | G m/l/pcs, manufacturing frozenversions; e217f77 общий adapter BOM/CSV/procurement, native/composed controls | Реальные документы клиента и межразмерностная плотность не подтверждены автоматически. |
| Оплата→выпуск→sale, ДДС отдельно отP&L | G/R cash once, release cost held asstock | Нет banklive attestations; клиентский cashopening остаётся вводом владельца. |

## Следующие конкретные программные задачи

1. **OzonAPI normalize — отсутствующий программный модуль и внешние данные**: отдельный источник версии денежного контракта, posting/item/nonitem/containerfees, signedcomponents↔source totals до копейки, identityoccurrence/correctionversions, realquantity/returnlinks, OzononlyFIFO. Нельзя включать normalizationverified только после пустых ответов; непустой реальный источник сейчас отсутствует. Ручной CSV/FIFO и raw API collection не заменяют полный normalizer.
2. **11 обучающих уроков — производство материалов**: актуальные экраны, учебные числа, сценарии, видео и проверка каждого урока по TRAINING inventory; существующий150s обзор не закрывает весь курс.
3. **Оставшаяся UIприёмка**: network-unknown UUID retry в браузере, заполненные мобильные таблицы/бизнес-действия, другие dialogs и фактическая screen-reader речь. Новый inline consent и historical opening/documents уже приняты CUA на учебных данных; их не возвращать в «код отсутствует».
4. **Внешние свидетельства**: реальные исторические партии/Ozon документы, 5–10 интервью, разрешение на публичный кейс и клиентская когорта для activation/D7/payment/churn. Синтетические тесты и агентские регистрации не заменяют эти свидетельства.
5. **Условный scope**: statuspage только с настоящим мониторингом, внешняя нишевая аналитика/storefront/fullbookkeeping/autonomousads не объявлены безусловными обязательствами исходного аудита. Рекламой занимается Claude по поручению владельца.

Внешние зависимости (реальные документы, интервью, разрешение на кейс, когорта) остаются явно открытыми. Завершённый программный пакет и подтверждённый deploy не равны закрытию этих доказательных требований. Документ не предлагает менять расходы/роли/состояния клиента, подделывать оплаты или возвращать нежелательные сравнения.
