# Ozon functional parity implementation plan

> **For agentic workers:** implement bounded independent tasks in parallel, with separate source ownership; root integrates, reviews, verifies and deploys committed HEAD.

**Goal:** Довести программные пробелы Ozon: денежная расшифровка/FIFO, операционные отчёты, рекламная аналитика и интерфейс; отдельно фиксировать реальную сверку.
**Architecture:** Существующие immutable raw snapshots и документальный Ozon FIFO сохраняются. Новые read models используют только Ozon namespace, Decimal и явное покрытие. WB не смешивается с Ozon; snapshot revisions не дублируют проводки. Интерфейс получает отдельные вкладки учёта Ozon, API маршруты изолированы от selected WB shop.
**Tech Stack:** Python/FastAPI/SQLite/Decimal, React/TypeScript, Seller/Performance API, tests, Docker verified patch deployment.
**Spec:** User2026-10-06: добить все перечисленные Ozon пробелы, существующая пауза больше не ограничивает разработку; реальные непустые начисления пока не предоставлены.

## Global constraints
- Данные отсутствуют != нулевые продажи/прибыль. Нет ложных verified/source_complete flags.
- Денежные поля только текущего первичного контракта Ozon; непонятная формула, неподтверждённые quantities/returns/identity отклоняются, не угадываются.
- Коррекции и повторные загрузки не удваивают результат. Материализованный preview отделён от immutable manual ledger; подтверждение требует fingerprint/current revision и owner/manager.
- Production writes в Ozon, цены, бюджеты, возвраты и отгрузки не выполняются. Только локальный код и изолированные учебные данные; реальные API read допустимы для принятого существующего подключения, без публикации секретов/финансовых значений.
- Сохранить 16 озвученных уроков, текущие tabs/light theme, security fixes и request memory budget; tenants1536MiB, Traefik не пересоздавать.
- Реальная денежная и налоговая сверка не объявляется завершённой по mock/пустым API responses. Налоги — existing calculator, документально подтверждённая база/явный ввод, оценка; не выводить из net_accrual.

### Task1: Financial source contract and normalization/FIFO bridge
Files: new tenant-app/db/ozon_normalization.py, tests/test_ozon_normalization.py; new tenant-api/ozon_normalization_api.py and tests; existing ozon_pnl helpers only if justified. Agent financial owns these, no App/api_server edits.
- [x] Inspect current official Seller finance schemas/sign/units/identity and record source evidence.
- [x] Failing fixtures: multiSKU, returns, independentfees, partialdata, corrections, repeats, unknowncurrency/types, exact Decimalreconciliation.
- [x] Implement safe normalized financial preview and documented movement/FIFO integration where contract supports; unsupported rows retain blockers.
- [x] Fingerprint/roles/local confirmation or replay boundary; tests preserve manualledger/WBstock.

### Task2: Ozon product/period insights
Files: new tenant-app/ozon_insights.py +tests; new tenant-api/ozon_insights_api.py +tests. Agent insights owns these.
- [x] Read latest immutable source + manual documentary ledger, resolve date/period/coverage.
- [x] Product economics, per-day heatmap and plan/fact/favorites with separate Ozon SKU namespace, bounded queries and explicit order-vs-sale semantics.
- [x] Monthly documentary P&L + CSV and tax estimate input bridge using confirmed revenue/explicit inputs, unknown taxbase stays unknown.
- [x] Roles, scope, cache freshness, missing/empty distinctions, duplication and isolation tests.

### Task3: Advertising analytics and credentials
Files: new tenant-app/ozon_ads_analytics.py +tests; new tenant-api/ozon_ads_api.py +tests; OzonAgentClient shared changes only after coordinating. Agent ads owns these.
- [x] Inspect official Performance API OAuth/report lifecycle; dedicated credentials, no placeholder zero metrics.
- [x] Bounded read/report poll + persisted complete snapshot; metricdefinitions/percampaign/days/CSV with missing attribution coverage.
- [x] Tests pagination, expiry, async status, timeout/429, no marketplace mutation, tenant secrets isolation.

### Task4: Cabinet integration/root
Files: src/OzonAccounting.tsx, new Ozon feature panels/.css/.tests; api_server imports, shop route scopes/fixture, Dockerfilecopies, memoryguard. Root owns integration.
- [x] Integrate typed routes with supported WB-shop boundary and local heavy budget; no circular import.
- [x] Ozon tabs: overview/finance/products/advertising, useful empty state, source coverage, no WB branding in Ozon results.
- [x] UI tests/mobile/lightdark/a11y, full suites and independent review.

### Task5: Release and final acceptance
- [x] Clean committed HEAD source/frontends; exact base image/source hashes, restored encrypted backup, candidate/rollback rehearsals.
- [x] Deploy demo/mvvorokov and provisioner pin, headers/media preserved.
- [x] Live healthy/memory/cold routes/no unexpected writes; report implemented features vs live-data acceptance dependencies.

Acceptance: runtime d4446faa326fb95929e8e65edb6d1cc108db058f deployed to both tenants. 978 tenant-app tests + 10 subtests (1 skipped), 578 API tests, 508 frontend tests; clean committed frontend compilation. Cold September finance 6.24s. Ozon real financial and Performance-report acceptance is explicitly pending, not converted into a passed gate. See docs/marketing/OZON_FUNCTIONAL_PARITY_2026-10-07.md.
