# Workcenter Calendar Implementation Plan

**Goal:** Minute-based loading by shop-floor workcenter, exact product variant and operation, with explicit assignments and reviewed rescheduling.
**Architecture:** Add independent workcenter/norm/assignment/history tables through the tenant schema migration. Assignments overlay execution task dates; existing execution tasks, facts and batch dates remain intact. Existing production-workspace router and role middleware host bounded endpoints; standalone React section provides configuration, loading and preview/confirmation.

- [x] Implement bounded center/norm validation, optimistic revisions and immutable assignment history.
- [x] Calculate weekday availability, remaining-unit operation minutes, overload, missing norms/unassigned tasks; never equate missing norm with zero load.
- [x] Preview explicit assignment/reschedule without writes; confirm checks full input fingerprint under an immediate transaction and refuses started/completed/factual tasks.
- [x] Add API role/demo protection, exact catalog variants and finite/range guards; wire React setup/loading/preview/confirm.
- [x] Verify DB conservation/history/stale changes, API authorization/ranges and UI explicit confirmation, full suites/build/gates and graph; scoped commit for parent image/deploy.


Verified: tenant330 plus8 subtests; full API334 (21 new real-database workcenter cases); frontend102 (2 new explicit-confirmation/read-only cases) and production build. Calendar reports only explicitly assigned operations, and warns about configured operations without assignments. Missing norms keep loading incomplete rather than treating it as zero. Assignments overlay dates and are not an automatic optimizer or a rewrite of original production facts.

Inherited source-gate caveat: committed base6760c90 lacks Docker COPY for ozon_accounting_api.py. Root already has its independent Docker fix in progress; this scoped workcenter branch deliberately does not include those unrelated dirty changes. Workcenter uses the already-copied production_workspace_api.py and db/ package. Parent must rerun source/image gates after integration and deploy.

SQL replacement follow-up: guard BEFORE INSERT for existing history primary key or request id, since SQLite INSERT OR REPLACE bypasses delete triggers with recursive triggers disabled. Two regressions preserve original journal row under both conflicts.
