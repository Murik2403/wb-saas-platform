# FBS Supply Actions Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make FBS supply cards in the redesign open their WB details and expose a safe QR/barcode action.

**Architecture:** Reuse the existing `WBAPI` client methods and tenant token storage. Add read-only tenant API routes that normalize provider failures into HTTP 4xx/5xx messages, then call those routes from the supply screen and render a small accessible dialog. No WB write operation is introduced.

**Tech Stack:** FastAPI, Python `unittest`/pytest, React/Vite, Vitest, TypeScript.

**Spec:** `docs/COMMERCIAL_RELEASE_AUDIT_2026-09-17.md`, P1 «Поставки».

## Global Constraints

- Never return WB tokens or provider request IDs.
- Do not perform real WB writes in tests; mock the client.
- Keep changes in `mh-redesign-lab` and shared `tenant-app/wb_api.py`; do not modify Streamlit UI.
- Run tenant-api tests, Vitest, and `npm run build` before deployment.

### Task 1: Read-only FBS detail API

**Files:**
- Modify: `mh-redesign-lab/tenant-api/api_server.py`
- Test: `mh-redesign-lab/tenant-api/tests/test_finance_api.py`

- [ ] Add `GET /api/v1/operations/supply/fbs/{supply_id}` and `GET /api/v1/operations/supply/fbs/{supply_id}/barcode`.
- [ ] Require a connected token, reject blank/overlong IDs, call `WBAPI.get_fbs_supply_details` / `get_fbs_supply_barcode`, and return sanitized JSON.
- [ ] Add OpenAPI and mocked-client tests for both routes and missing-token behavior.

### Task 2: Redesign data functions and dialog

**Files:**
- Modify: `mh-redesign-lab/src/data-source.ts`
- Modify: `mh-redesign-lab/src/App.tsx`
- Test: `mh-redesign-lab/src/App.test.tsx`

- [ ] Add typed fetch helpers for both read-only routes.
- [ ] Add «Подробнее» and «QR/стикер» actions to FBS supply rows.
- [ ] Render loading, empty, provider-error, and detail states in an accessible dialog; do not show raw response blobs.
- [ ] Add Vitest coverage for the FBS actions and dialog error state.

### Task 3: Verification and release evidence

**Files:**
- Modify: `docs/RELEASE_GAP_AUDIT_2026-09-17.md`
- Modify: `docs/RELEASE_EVIDENCE_2026-09-17.md`

- [ ] Run tenant-api tests, Vitest, build, `git diff --check`, and `graphify update .`.
- [ ] Deploy only after all checks pass and verify health plus route status in the existing production tenant.
- [ ] Record the image digest and explicitly note that real WB writes and physical-device acceptance remain manual.
