# Operational Screen Completion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace empty and static operational dashboard blocks with payloads calculated from tenant data and openable detail views.

**Architecture:** The FastAPI dashboard endpoints will expose derived stock, production, supply, and finance panels from the same tenant tables already used by the calculation layer. The React shell will turn rows and quality checks into buttons that open a common detail drawer or navigate to the relevant settings section; it will not invent data where WB does not provide it.

**Tech Stack:** FastAPI, pandas, React, TypeScript, Vite, pytest.

**Spec:** User screenshots and requirements from 2026-09-10.

## Global Constraints

- Show only values backed by tenant calculations or a clearly marked unavailable state.
- Keep writes behind the existing explicit forms and confirmation flows.
- Preserve the current period contract for financial and product data.
- Run API tests and the Vite production build before completion.

---

### Task 1: Populate stock, production, supply, and finance operational panels

**Files:**
- Modify: `mh-redesign-lab/tenant-api/api_server.py`
- Modify: `mh-redesign-lab/src/data-source.ts`
- Modify: `mh-redesign-lab/src/contracts.ts`
- Test: `mh-redesign-lab/tenant-api/tests/test_audit_regressions.py`

**Interfaces:**
- Produces `coverage`, `rules`, `stages`, `materials`, `cash_windows`, and `acceptance_windows` in existing dashboard responses.
- Consumed by the adapter layer without mock fallbacks.

- [x] Write API regression tests with a compact dashboard fixture and assert populated response arrays.
- [x] Run the focused tests and verify they fail because the responses contain empty arrays or lack the fields.
- [x] Derive warehouse coverage and replenishment rules from products, stock days, and the current stock snapshot.
- [x] Derive production readiness stages and material coverage from production settings, material inventory, and recommended production tasks.
- [x] Derive cash windows from the financial dashboard period and label unavailable WB acceptance slots explicitly when no booking API is connected.
- [x] Run the focused tests and verify the payload values and labels.

### Task 2: Make dashboard evidence openable

**Files:**
- Modify: `mh-redesign-lab/src/App.tsx`
- Modify: `mh-redesign-lab/src/style.css`
- Test: `mh-redesign-lab/src/App.test.tsx` or existing component test location if present

**Interfaces:**
- Produces `DetailDrawer` with `title`, `subtitle`, and a list of labelled facts.
- Existing cards call `openDetail` without mutating operational data.

- [x] Write a component test that clicks a product card and a data-quality card and asserts the detail drawer content.
- [x] Run the focused component test and verify it fails because cards are plain articles.
- [x] Add the reusable drawer and keyboard-accessible button semantics to product economics cards, stock rows, production rows, supply rows, and control cards.
- [x] Route production-norm and product-cost quality cards to a distinct settings summary block.
- [x] Run the focused component test and verify that focusable cards open their details.

### Task 3: Make the settings summaries discoverable

**Files:**
- Modify: `mh-redesign-lab/src/App.tsx`
- Modify: `mh-redesign-lab/src/style.css`
- Test: `mh-redesign-lab/src/App.test.tsx` or existing component test location if present

**Interfaces:**
- Produces anchor targets `#production-norms` and `#product-costs` on the existing settings sections.
- The control page exposes a separate `Настройки расчётов` block with live counts and navigation buttons.

- [x] Write a component test for the settings shortcuts.
- [x] Implement the separate settings summary block and anchors.
- [x] Verify navigation keeps the selected settings section visible.

### Task 4: Verify all visible routes

**Files:**
- Test: `mh-redesign-lab/tenant-api/tests/test_audit_regressions.py`
- Test: `mh-redesign-lab` production build

- [x] Run the full tenant API suite.
- [x] Run the TypeScript check and Vite production build.
- [x] Restart local services and inspect stock, finance, production, supply, reports, products, and control in the browser.
