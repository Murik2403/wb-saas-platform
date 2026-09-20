# Calculator Percentage Bases Implementation Plan

> **For agentic workers:** implement this plan task-by-task and verify each task before continuing.

**Goal:** Make commission and tax percentage semantics explicit in the public calculator while preserving existing links and per-unit behavior.

**Architecture:** Keep the calculator browser-only. Ruble articles continue to support `per_unit` or `total`; commission remains a percentage of selling price, and tax gains an explicit `per_unit`, `total`, or `revenue_pct` basis. CSV and permalink must carry the chosen basis.

**Tech Stack:** Static Jinja template, inline browser JavaScript, Node regression test.

**Spec:** `gateway/logic/unit_economics.py` allocation bases and existing public calculator contract.

## Global Constraints

- Existing URLs without basis parameters must remain valid and mean the current behavior.
- Frozen capital remains displayed separately and never reduces profit.
- The calculator must continue to state that it is not accounting profit and does not send data to the server.
- No WB API or database changes in this slice.

### Task 1: Explicit tax basis and percentage regression coverage

**Files:**
- Modify: `gateway/templates/marketing_calculator.html`
- Modify: `gateway/tests/test_marketing_calculator.cjs`

- [x] Add a tax basis selector with `per_unit`, `total`, and `revenue_pct` options.
- [x] Apply the selected tax basis in the per-unit profit calculation; keep commission as price percentage.
- [x] Persist and restore `tax_basis` in the permalink and include the basis and allocated amount in CSV.
- [x] Add tests for total tax allocation, revenue percentage tax, old links without `tax_basis`, and CSV output.
- [x] Run the Node calculator regression and the relevant Python marketing/SEO tests.

### Task 2: Release verification

**Files:**
- No source changes.

- [x] Run the full gateway test suite and `git diff --check`.
- [x] Build the gateway image from the commit and verify the image revision.
- [x] Back up SQLite and verify `PRAGMA integrity_check` is `ok`.
- [x] Switch the gateway container, verify health, `/healthz`, live calculator HTML, and absence of `1С`, `vs-1c`, and `сравнение с`.
