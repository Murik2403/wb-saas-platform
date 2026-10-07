# Cabinet Lazy Loading Implementation Plan

> For agentic workers: implement task-by-task with independent verification; the root agent owns App.tsx integration and production deployment.

**Goal:** Delay downloading heavy panels until their section is opened, preserving forms, marketplace authorization, deep links and lesson playback.

**Architecture:** React.lazy at real component boundaries with a local Suspense fallback and a recoverable chunk-load error boundary. Keep identity/context validation outside all lazy content. Existing WorkspaceTabs mounts only active panels or previously visited keepMounted panels; retain that behavior.

**Tech Stack:** React, TypeScript, Vite 7, Vitest 5.

**Spec:** `docs/CODEX_HANDOFF_AUDIT_2026-10-07.md`, P1 frontend bundle performance.

## Global Constraints

- App.tsx is owned by root; this plan makes no source changes to it.
- Do not replace static imports with manualChunks alone: eagerly imported chunks would still download at startup.
- Shared ManufacturingCosts, CashflowPanel and CashAccountsPanel are also statically imported by App; lazy Ozon imports alone cannot remove them from the entry bundle.
- Do not lazy-import authorization/context helpers or defer validation. Clear marketplace/shop context still aborts pending API work immediately.
- Preserve SettingsWorkspace and form keepMounted behavior, especially typed incident and price drafts.
- Avoid blanket component prefetch on mount or pointer movement; it would erase the network benefit.
- Build sourcemaps below were local measurements only; do not publish them to the site.

## Measured Baseline (2026-10-07 working tree)

`npm run build -- --sourcemap` passed including tsc; 1,947 transformed modules. One entry JS: **957.15 kB / gzip 259.80 kB**; CSS **136.31 kB / gzip 24.68 kB**. JS map 2,831.35 kB is a local measurement artifact. Vite warns about the entry over 500 kB.

Source-map sourceContent UTF-8 sizes are decomposition clues, not transfer savings: App.tsx 303,036 B; data-source.ts 139,127 B; ManufacturingCosts.tsx 46,021 B; TaxReconciliationPanel.tsx 27,648 B; ProductEconomicsList.tsx 21,020 B; ProductionWorkspace.tsx 20,271 B; SharedWarehouse.tsx 19,988 B; MigrationImport.tsx 18,906 B; RnpPlanFact.tsx 18,738 B; OzonCabinetViews.tsx 18,392 B. Ozon-named modules total 105,428 B. Project sourceContent total 1,108,391 B. No percentage improvement is promised before an experimental split is measured.

Graphify query first confirmed App -> OzonCabinetShell -> OzonCabinetViews and its static panel dependencies. WorkspaceTabs delays mounting/query effects but does not delay static module loading. The word lazy in useProductsTrend describes data loading, not dynamic code import.

## Task 1 — Ozon panel boundaries without App edits

- [ ] Add `src/LazyPanel.tsx` with accessible loading status, a scoped error boundary and explicit user retry/reload for chunk failures. Do not silently reload and lose drafts. Add `src/LazyPanel.test.tsx` with a deferred module, rejected module and preserved unrelated content.
- [ ] In `src/OzonCabinetShell.tsx`, replace runtime OzonCabinetViews import with module-scope React.lazy mapped named export; keep `OzonCabinetPage` as import type. Wrap only the page content with LazyPanel; navigation and period controls remain usable while downloading.
- [ ] In `src/OzonCabinetViews.tsx`, dynamically import Ozon-only heavy panels: OzonProductInsights, OzonAdsAnalyticsPanel, OzonMonthlyPanel, OzonHistoricalPnl, OzonNormalizationPanel, OzonRealizationSource, OzonPriceControls. Use named-export adapters matching their existing props. Mount each lazy wrapper only in its current page/tab/disclosure, preserving current abort cleanup.
- [ ] Tests in OzonCabinetShell.test.tsx and OzonCabinetViews.test.tsx: unopened finance/products/ads do not execute loader or send requests; direct finance URL opens finance after module resolves; manager/viewer/demo flags remain intact; marketplace switch during import never mounts stale Ozon content.
- [ ] Measure a build and actual browser network on WB Today and Ozon Today, then opening one target page. OzonCabinetViews chunk must not load on WB; finance-only Ozon chunks must not load on Ozon Today. Shared App imports may remain entry code by design.

## Task 2 — Largest WB gains require root App integration

- [ ] Root replaces static heavy named imports in App with module-scope lazy adapters for tax, production, products analytics/RNP, migration/import and FBS/self-buyouts. Define fallbacks near each existing WorkspaceTabs panel, not around the whole app shell.
- [ ] Before extracting App view functions, document exact props/state/callback ownership. Do not move all App state or data-source into one asynchronously loaded shell and call that page splitting.
- [ ] Keep Today navigation/context/period/owner UI available synchronously. Defer a dialog component only after its open action, with accessible loading and focus restoration if import fails.
- [ ] Regression: form values survive tab switching; hash deep-link selects correct tab; initial route, back/forward and mobile navigation work; owner-only connect, manager cost/norm writes remain verified; unopened panels make no network calls.

## Task 3 — Acceptance and committed release

- [ ] Run targeted deferred-import tests, full `npm test -- --maxWorkers=4`, `npx tsc -b --pretty false`, then production build.
- [ ] Record entry/chunk raw and gzip sizes and browser network requests for first screen and opened targets. Do not confuse source byte counts with minified savings or API request latency.
- [ ] Verify both themes at 320/390/desktop widths and a deliberately failed chunk request. Loading/error panels must not stretch the page or obscure navigation.
- [ ] Root builds/deploys committed HEAD and checks public demo and real cabinet read-only. Preserve voiced lesson mounts/catalog and gateway authorization. Report exact release revision and live evidence.

## Current Status

First bounded runtime step implemented without App edits: OzonCabinetShell imports Views asynchronously; seven Ozon-only panels have separate imports and scoped Suspense/error handling. LazyDisclosure delays financial document panels until expansion, then keeps visited forms mounted. A failed chunk leaves the shell usable; the refresh button warns to save drafts and never refreshes automatically. Imports retain validated session props and existing APIs; there is no mock-data fallback.

Fresh `npm run build -- --sourcemap` passed tsc and Vite. Entry JS **880.81 kB / gzip 241.04 kB**, down **8.0% raw / 7.2% gzip** from baseline; entry CSS **123.90 kB / gzip 22.81 kB**. Eight asynchronous JS chunks range from 5.37 to 18.34 kB. Local HTML references only entry JS/CSS, with no eager modulepreload of Ozon chunks. This does not close the whole bundle issue: App and shared heavy panels remain eager and the main JS still exceeds 500 kB. Browser network/live acceptance and committed deployment belong to root's final release gate.

New tests cover deferred loader/fallback, rejected chunk with unrelated shell still available, unopened disclosure loader not called, draft preserved after close/reopen, and pending import resolving after unmount not restoring stale content. Existing shell/view role and navigation tests adapted to asynchronous resolution; first targeted gate passed 25 tests in three files. Role UI regression suite previously passed 49 tests in five files.
