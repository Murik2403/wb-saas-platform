# Tenant Action Executor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** New plan tasks are claimed and completed by a tenant-local AI executor instead of remaining status-only records.

**Architecture:** The redesigned API owns plan records and invokes a tenant-local executor after a task is created or explicitly started. The executor records a structured result and history; it completes read-only campaign checks from the tenant database and marks tasks requiring unavailable inputs as blocked with an explanation.

**Tech Stack:** Python, FastAPI, SQLite, React, TypeScript, Vite, pytest.

**Spec:** User request on 2026-09-09: tasks in every tenant plan must be performed by the in-product AI executor rather than hanging in the queue.

## Global Constraints

- Each tenant process operates only on its own SQLite database.
- The executor must never change an external marketplace account without a separate user confirmation flow.
- Every run must leave an audit trail visible in the plan drawer.

---

### Task 1: Persist executor state and task results

**Files:**
- Modify: `mh-redesign-lab/tenant-api/api_server.py`
- Test: `mh-redesign-lab/tenant-api/tests/test_action_plan_executor.py`

**Interfaces:**
- Produces plan rows with `executor`, `result`, `started_at`, and `completed_at` fields.

- [ ] Write a failing API test that creates a plan task and asserts executor fields are returned.
- [ ] Add backward-compatible SQLite column migrations in `_init_redesign_schema`.
- [ ] Run the focused test.

### Task 2: Execute read-only campaign checks

**Files:**
- Create: `mh-redesign-lab/tenant-api/action_plan_executor.py`
- Modify: `mh-redesign-lab/tenant-api/api_server.py`
- Test: `mh-redesign-lab/tenant-api/tests/test_action_plan_executor.py`

**Interfaces:**
- Consumes a task id and a tenant-local `compute_ads` callback.
- Produces a completed `Проверить` task containing the matching campaign metrics, or a blocked task with a concrete reason.

- [ ] Write failing tests for successful campaign analysis and a missing campaign.
- [ ] Implement a single-flight worker that claims tasks, records its state, and writes result/history atomically.
- [ ] Start the worker after task creation and after the user presses `В работу`.
- [ ] Run focused tests.

### Task 3: Show executor ownership and outcome

**Files:**
- Modify: `mh-redesign-lab/src/data-source.ts`
- Modify: `mh-redesign-lab/src/App.tsx`
- Test: Vite production build.

**Interfaces:**
- Consumes optional executor fields from the action-plan API.
- Produces plan cards with the executor, result, and a `Нужны данные` state for blocked work.

- [ ] Extend action-plan types with executor result fields and `blocked` status.
- [ ] Replace manual-only `В работу` interaction with a launch action and display the latest result in task details.
- [ ] Build the frontend and run all tenant/API tests.
