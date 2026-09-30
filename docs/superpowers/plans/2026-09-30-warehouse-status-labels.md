# Warehouse status labels implementation plan

> **For agentic workers:** Execute this bounded plan task by task; independent source review is delegated to the existing reviewer.

**Goal:** Display Russian warehouse states and avoid promising another attempt for a saved disabled schedule.

**Architecture:** Presentation-only mappings in SharedWarehouse and a saved-enabled conditional in ScheduleForm. Preserve all API values, UUID identities, source selection, ACL and scheduler semantics.

**Tech Stack:** React, TypeScript, Vitest and Testing Library.

**Spec:** Root's actual2055 browser findings: reserved/inbound/wb remain English and disabled PDF shows a next attempt timestamp. Root explicitly defers this patch to the next release.

## Global constraints

- Base2055a4ac is frozen and currently undergoing release; only the isolated warehouse-labels worktree may change.
- Unknown enum values render an explicit Russian unknown label, never a fabricated successful state.
- Next-attempt text reflects saved schedule.enabled, not unsaved checkbox state.
- No backend, money, source provenance, transport, permission or production changes.
- Graphify query was attempted first; this worktree has no graph.json, so scope tracing uses the two actual source components and backend enum declarations.

## Task1: Russian state presentation

Files: SharedWarehouse.tsx and SharedWarehouse.test.tsx.

- [x] Add component regression with reservation.status='inbound', hold.status='held', document.kind='sale'; assert Russian visible text and unchanged select option identity.
- [x] Run focused regression and observe untranslated state failure.
- [x] Add fixed presentation maps: reserved→Зарезервировано, inbound→В пути, wb→На WB, released→Резерв снят; holds held→В карантине/released→Карантин снят. Translate actual reserve/commit/release/receipt/sale/return document kinds and quarantine preview locations.
- [x] Run focused tests; preserve raw status comparisons and request construction.

## Task2: Disabled next attempt

Files: SecondaryShopSchedules.tsx and SecondaryShopSchedules.test.tsx.

- [x] Add regression with disabled saved schedule and nonnull next_due_utc; assert «Расписание выключено — следующая попытка не назначена.» and no timestamp promise.
- [x] Add enabled case proving saved enabled still shows timestamp; an unsaved off checkbox must not relabel server state.
- [x] Change only the rendered paragraph conditional on schedule.enabled.
- [x] Run focused component suites, full frontend gate and build; independent source review before exact scoped commit.

## Completion boundary

Source/tests only; root may integrate in a later release after deployment of2055. No runtime/browser claim for this patch until that later release gate.

## Verification evidence

- Focused red gate: 3 failures /15 passes before implementation; green18/18 after implementation (one ambiguous existing table/header selector corrected to cell role).
- Full frontend:205 tests/35 files passed; build exit0; diff-check clean.
- Independent reviewer:18 tests/2files passed, presentation scope accepted.
- No new deployment or browser acceptance for this polish patch; existing2055 positive/negative browser proofs belong to unchanged source2055.
