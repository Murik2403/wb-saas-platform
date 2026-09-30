# Cabinet UX Implementation Plan

> **For agentic workers:** Execute independent view tasks with subagents; root integrates, reviews and deploys the verified release.

**Goal:** Implement the cabinet structure audit without changing accounting or customer data.

**Architecture:** Keep existing view handlers and data sources. Group mounted panels with accessible navigation and native disclosures, retaining drafts when sections are hidden. Scope new form styles to production/settings and compact the shared header.

**Tech Stack:** React 19, TypeScript, Vite, Vitest, existing tenant runtime.

**Spec:** `C:/Users/mvvor/Downloads/wbsaasplatform/wb-saas-platform-clean/docs/CABINET_UX_AUDIT_2026-09-30.md`.

## Global Constraints

- Do not alter calculations, formulas, FIFO, API contracts, landing pages or demo numbers.
- Do not modify the dirty main checkout; work on `codex/cabinet-ux-20260930` from the verified release checkout.
- Preserve error/partial-data warnings, permissions, unsaved form state and existing actions.
- Verify all 11 sections, 1440/1024/390 widths and the three long pages before claiming visual acceptance.

## Task 1: Finance — ux_finance

Files: `mh-redesign-lab/src/App.tsx` FinanceView/FinanceFifoDetailTable only; own structure tests/styles.

- [x] Group summary and WB balance, money and adjacent expense share, cash controls, collapsed cost verification.
- [x] Keep preliminary-data status visible; disclose technical identifiers only in detailed tables.
- [x] Test DOM order, default collapse and preserved amounts/actions; run existing finance source and summary tests.

## Task 2: Settings — ux_settings

Files: SettingsViewV2 span, new SettingsWorkspace, SyncHistory and OnboardingPanel with backward-compatible props.

- [x] Put heading and incomplete onboarding before four categories.
- [x] Move CSV into Data; preserve mounted panels and drafts.
- [x] Show three latest backups and disclose the remainder.
- [x] Test category selection, form-state preservation and backup disclosure.

## Task 3: Production — ux_production

Files: ProductionView span, ProductionWorkspace, ManufacturingCosts, scoped CSS and calendar presentation.

- [x] Separate shift, WIP/materials, capacity/calendar and composition/cost.
- [x] Preserve mounted state; style existing forms; explain read-only access above forms.
- [x] Disclose empty calendar days; test category visibility and preserved actions.

## Task 4: Other views and common presentation — root

Files: remaining App.tsx regions, new cabinet presentation helpers/tests, styles.css and display-only data-source strings.

- [x] Give the header full readable titles and icon buttons with accessible labels/tooltips.
- [x] Remove duplicated connection warnings and raw transport paths from normal user copy without suppressing data-quality warnings.
- [x] Compact incident creation and completed checks; rank critical checks first.
- [x] Correct Russian plurals and report timestamps; consolidate repeated inventory signals and compact empty reports/agent queue.
- [x] Explain negative margin and advertising ratios; retain original numbers and actions.

## Task 5: Integration, acceptance and deployment — root

- [x] Review diffs for forbidden logic changes; run all frontend tests and production build.
- [x] Use isolated synthetic browser fixture for all 11 pages and category interactions; measure height and overflow at specified widths.
- [x] Commit only owned changes; build fresh exact-source assets/image and verify source bytes.
- [x] Restore fresh encrypted backup before switching; preserve rollback, verify live APIs/assets and provisioner pin.
- [x] Record exact deployed revision, test results and any unverified acceptance criteria.
