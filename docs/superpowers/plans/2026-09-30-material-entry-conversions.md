# Material entry conversion implementation plan

**Goal:** convert CSV opening and procurement receipt quantities into an existing material's catalog unit, and inversely convert price while preserving exact source total.

**Architecture:** reuse the same-dimension Decimal adapter; add price/provenance conversion with canonical quantity precision3 (physical writer) and rate precision6 (FIFO). Source order and parsed CSV identity remain unchanged; confirmed normalized rows and append-only receipt journals retain both original and canonical values.

**Spec:** bounded F101 follow-up approved by root on 2026-09-30; BOM increment is 74fdb25. No roll/density/package inference; existing roll branch remains unchanged.

- [x] Tests first: opening grams→kg priceinverse/total/replay/staleunits/atomicdenial; receipt partial currency conversion→FIFO/WIP/undo/replay and dimensional denial.
- [x] Shared amount conversion rejects unrepresentable precision and Decimal→float fraction loss. No rounding of converted totals.
- [x] CSV preview computes conversions against actual catalog; confirm reparses original identity, rechecks preview, applies current normalized rows; snapshot includes provenance. Identity imports retain existing content hashes.
- [x] Procurement SAVEPOINT preserves original item quantity/pricing/payment; normalize explicit nonroll quantities to catalog base and inverse landed price. Converted movement-linked journal is immutable. Newly created explicit quantity material keeps its declared unit; legacy roll defaults unchanged.
- [ ] Focused tenant/API regressions and source review; root full suites, release gates and deployment.

Validation: 128 tenant tests passed (10.87 s), covering conversion utility, CSV source hash vs canonical preview, stale catalog denial, historical import, BOM, shared manufacturing, nested planning, procurement currency/partial receipt, exact 999 g stock, known 25.5 m packaged representation, reversal and actual WIP FIFO. 43 API regression tests passed (8.90 s); full suites/deployment remain root gates. Converted receipt physical updates use exact Decimal representation rather than legacy tolerance carry; source-unit rolls and existing same-unit receipt writer are unchanged.
