# Full Product Audit and Mobile Release Plan

**Goal:** Make every dashboard section usable end to end, preserve real tenant data, and ship an installable phone experience.

## 1. Inventory and regression matrix

- Enumerate every page, card, control, form, modal, and API call.
- Map every visible action to a real navigation, read, or write outcome.
- Record desktop and mobile acceptance checks for all sections.

## 2. Data and API verification

- Smoke-test every dashboard read endpoint for all deployed tenants.
- Exercise write workflows against an isolated copy of tenant data.
- Fix timeouts, misleading empty states, malformed responses, and broken downloads.

## 3. Section-by-section repairs

- Today and Stock: signals, revenue pace, filters, SKU rows, FIFO actions.
- Ads and Finance: periods, campaigns, drill-downs, ledgers, and exports.
- Production and Supply: shifts, execution, materials, suppliers, receipts, and statuses.
- AI agents, Reports, Products, Control, and Settings: all cards, forms, schedules, channels, edits, and maintenance actions.

## 4. Shared interaction model

- Replace generic or dead dialogs with the correct detail, form, or destination.
- Make interactive cards keyboard accessible and visibly interactive.
- Prevent duplicate submits and show success or actionable error results.

## 5. Phone layouts

- Verify 390 px, 430 px, tablet, and desktop widths.
- Remove overflow, clipped controls, oversized empty areas, and uneven columns.
- Keep primary actions reachable and use mobile navigation for every section.

## 6. Installable mobile app

- Add a web app manifest, icons, install metadata, and service worker.
- Cache the application shell without caching tenant API responses.
- Provide a clear offline/error state and safe update behavior.

## 7. Release verification

- Build the frontend and run backend regression tests.
- Run automated API and browser smoke checks across all sections.
- Deploy one immutable image to every tenant and verify health and key flows.
