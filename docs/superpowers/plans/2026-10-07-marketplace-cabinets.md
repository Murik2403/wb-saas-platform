# WB / Ozon cabinet switching implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Scoped parallel agents implement independent backend, transport and UI units; root owns integration and release.

**Goal:** Switch the whole cabinet between WB and Ozon, with separate data and actions on the same navigation and useful shared manufacturing/cash tools.

**Architecture:** A marketplace boundary mounts either the existing WB app or a separate Ozon shell, so WB dashboard hooks and drawers never mount in Ozon mode. Ozon uses a server-authorized primary seller context and explicit request header, bounded source snapshots/documentary reports and dedicated Performance credentials; approved shared operations remain labelled as common to the tenant.

**Tech Stack:** React/TypeScript, FastAPI, SQLite, existing Ozon Seller/Performance adapters, current Docker runtime.

**Spec:** User2026-10-07: «возможность прям переключаться между wb и ozon и наравне пользоваться кабинетами».

## Global constraints
- Preserve secondary WB-shop ACLs; Ozon primary seller access must not be granted merely by a URL, header or localStorage change.
- Switch cancels requests and rejects delayed response/body decoding; drafts, caches, URLs and forms cannot cross a marketplace boundary.
- Source or capabilities missing != zero, completed operation or verified profit. Explicit unknowns and source dates.
- No real price, budget, order, refund or shipment writes during testing. New mutation functionality needs primary-contract evidence, roles and explicit user confirmation in its UI.
- Production/materials/physical stock/cash tools are common tenant resources, never duplicated or relabelled as marketplace data.
- Existing course16 videos/captions, themes, tabs, request memory guard, tenant1536MiB and provisioner alias preserved.
- Release only committed source; encrypted backup/restore, candidate/rollback and live health/route/resource checks; preserve foreign TrueStats draft.

### Task1: Marketplace context and transport
Files: new src/marketplace-context.ts and tests; modify selected-shop.ts with focused tests; new tenant-api/marketplace_context_api.py and tests. Root later wires middleware/router/scopes/COPY.
Interface: GET /api/v1/marketplace-context/session?marketplace=wb|ozon returns {marketplace,role,can_write,is_demo,authorized,shared_routes:[{method,path}]}; frontend validateMarketplaceSession, selectMarketplace(session), currentMarketplace(), clearMarketplaceContext(), marketplaceFetch(input,init). Explicit X-MH-Marketplace:ozon on Ozon requests; only primary-shop0 headers. Middleware marketplace_guard(request) rejects mismatched/unsupported paths before handlers.
- [x] Test primary access, unknown selectors, secondary-shop mismatch, demo and role boundaries.
- [x] Implement context and abort/body guards; shared allowlist limited to confirmed tenant_shared routes.
- [x] Verify no wrong-marketplace network request or cache/draft replay on switch.

### Task2: Ozon cabinet operational read models
Files: new tenant-app/ozon_cabinet.py, tenant-api/ozon_cabinet_api.py and tests; ozon_accounting_client.py only where primary docs justify corrections.
Interface: GET /api/v1/ozon-cabinet/overview|stock|orders|returns|control?start_date&end_date. Responses {marketplace:'ozon',loaded,synced_at,items?,metrics?,blockers,coverage}; orders support kind=fbs|fbo|all. Raw source identifiers/statuses and current inventory timestamps retained; no guessed currency/unit scaling or posting identity.
- [x] Validate current official Seller source endpoints and pagination against existing snapshot collector.
- [x] Implement bounded SKU stock/posting/return/control read models and document-backed overview metrics.
- [x] Test missing/stale sources, duplicate/conflicting postings, SKU identity, stock scope, raw statuses and unknown money.

### Task3: Ozon cabinet views
Files: new src/OzonCabinetViews.tsx/.css/.test.tsx. Root supplies shell/navigation/date/role props.
Interface: OzonCabinetViews({page,start,end,canWrite,isOwner,role}) renders matching view; pages today,stock,ads,finance,production,supply,agents,reports,products,control,settings. Consume Task2 routes and existing Ozon panels. Reuse approved shared components without WB KPIs; expose separate Seller and Performance connections.
- [x] Render Ozon overview/stock/FBS-FBO/returns/control and existing products/finance/ad reports under the same page destinations.
- [x] Integrate common manufacturing/materials/cash components with explicit common-resource label; unsupported external operations cannot appear as successful mock buttons.
- [x] Test roles, transitions, nonempty/empty/null values, both themes and320px layout.

### Task4: Root integration and release
Files: new MarketplaceBoundary/OzonCabinetShell and tests/CSS; App.tsx entrypoint; api_server middleware/router; Docker copies; shop scopes/fixture; memory guard.
- [x] Selector present in WB/Ozon, URL/deep-link preservation, independent reload-safe period and page state; no WB hooks/dialogs in Ozon shell.
- [x] Integrate interfaces, independent review, full tests and browser navigation/transport acceptance.
- [ ] Commit, clean frontend build, exact immutable image bases, backup restoration/rehearsals, deploy demo/mvvorokov and future pin.
- [x] Record actual supported operations and remaining real-data/API acceptance explicitly; no claim of full platform operation parity from a selector alone.

### Task5: Ozon price drafts and explicit operations
Files: tenant-app/ozon_price_operations.py; tenant-api/ozon_prices_api.py; src/OzonPriceControls.tsx/css/tests. Separate price agent owns these, root wiresroute/COPY/context.
Interface: GET /api/v1/ozon-prices and /export.csv; POST /refresh,/preview,/confirm. Current primarySeller contracts: /v5/product/info/prices and /v1/product/import/prices.
- [x] Cached exact Decimal prices and independent Seller identity; bounded explicit refresh, no hidden external reads from GET.
- [x] Preview validates product identity/price/sourcefingerprint; explicit confirmation and local idempotency ledger before single upstream write, ambiguous timeout never retried or labelled successful.
- [x] Useful Products price drafts/CSV and Agents recommendation workflow; mocks only for upstream mutations.
- [x] Roles, partialresults, precision, staleness,429, repeated/ambiguous actions and frontend tests.
