# Inline CSV confirmation Implementation Plan

> **For agentic workers:** Execute this scoped change inline; parent reviews and deploys the joint release. Steps use checkbox syntax.

**Goal:** Require visible explicit approval before CSV import without opening a native JavaScript modal that stalls embedded-browser input.

**Architecture:** Keep preview and immutable retry request in MigrationImport. A controlled checkbox confirms the reviewed rows. Every input or role change invalidates approval; selected-shop navigation reloads the application and scopedFetch prevents stale-shop writes.

**Tech Stack:** React 19, TypeScript, Vitest, Testing Library, documented CUA browser API.

**Spec:** Parent work order: MigrationImport.tsx and tests only; preserve server validation, UUID replay and double-click lock.

## Global Constraints

- Do not change API, financial calculations, customer data or permissions.
- Never replace explicit consent with automatic approval.
- Exact32 browser reproduction: select/fill/preview work; clicking confirmation opens window.confirm then CDP mouse/focus commands time out.
- Native modal tooling incompatibility remains a hypothesis until fresh browser acceptance; do not claim source fix alone proves it.

### Task 1: Replace native modal with visible reviewed approval

**Files:** Modify `mh-redesign-lab/src/MigrationImport.tsx`; test `mh-redesign-lab/src/MigrationImport.test.tsx`.

**Interfaces:** Existing Input/Preview and `/preview`, `/confirm`, `/history` remain unchanged.

- [x] Add tests: confirmation disabled until checkbox checked; no native confirm invoked; source/mapping/kind/role/new-preview changes clear consent; uncertain retry preserves UUID and exact body; rapid double click creates one request.
- [x] Run `npm test -- src/MigrationImport.test.tsx` and capture the expected failure before implementation.
- [x] Add controlled consent state, reset it alongside preview invalidation, render reviewed row count plus clear no-replacement statement, and guard confirm with consent.
- [x] Run focused tests, full frontend tests and production build. Read outputs before declaring success.
- [ ] Commit only this plan and the two owned source/test files. Parent independently reviews and deploys.
- [ ] Retest actual checkbox/confirmation/history and drawer keyboard controls with documented CUA APIs in a fresh isolated fixture. Save honest proof outside Git; never substitute direct HTTP for UI acceptance.
