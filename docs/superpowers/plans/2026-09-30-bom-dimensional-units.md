# BOM dimensional units implementation plan

**Goal:** enter raw-material BOM norms in grams/kilograms, millilitres/litres or millimetres/centimetres/metres while existing FIFO and planning consume the catalog base quantity.

**Architecture:** `db/material_units.py` performs exact Decimal conversion within a dimension. Only newly appended BOM versions carry entered quantity/unit/factor and canonical quantity strings. Existing quantities/unit fields remain the downstream contract; no historical version or release is rewritten.

**Spec:** bounded F101 audit increment approved in agent coordination on 2026-09-30. This increment covers BOM entry only, not procurement or CSV opening conversions.

## Constraints

- Preserve exact custom catalog-unit identity; no conversion of unknown units.
- No density, area/width, roll-length or package inference. Semi-finished items retain `шт`.
- Base quantity is 0.001–1e12 with at most six decimal places; no silent rounding. Reject quantities whose decimal fraction cannot survive existing float storage.
- API retains existing finite numeric input compatibility. Preserve numerical entry as a decimal string; lexical formatting/trailing zeros are not guaranteed by numeric JSON transport.
- Permissions, shared ownership and movement identity remain unchanged. Root owns deployment after full-suite/release checks.

## Completed tasks

- [x] Utility regression tests first: missing utility failed collection; exact conversion and incompatible/unknown/nonfinite/base-precision cases added.
- [x] Backend normalizes before raw-material base bounds; append provenance to new `materials_json` only.
- [x] UI selector restricts each material to its dimension, resets when material changes, submits entered quantity/unit, displays stored entered=base recipe.
- [x] Actual release tests verify base physical/FIFO decrement and cost for mass, volume and length, backward conversion, old recipe preservation, idempotent replay and atomic denied save.
- [x] API tests verify 125 g → 0.125 kg → 4-unit production cost 5.25 RUB and stock 19.5 kg, incompatible litres rejected.
- [x] 82 DB/downstream tests, 26 API tests, 19 frontend tests passed; frontend build passed. The existing large-chunk warning remains.
- [ ] Root full-suite/release deployment and live acceptance.
- [ ] Separate F101 increment: CSV opening and procurement receipts with inverse price conversion/provenance/replay checks.
