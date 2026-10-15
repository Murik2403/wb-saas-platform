# Benchmarks: what is kept where

Files here are measurements, not claims. Nothing below is rewritten when a newer run exists: a negative result stays as it
was recorded, next to the document that explains it.

## Negative / refused results (kept separate, unchanged)
These are the 20 / 25 / 30 tenant density attempts on the 8 GiB local Docker stand. They are **not** passed acceptances.

| File | What it records |
|---|---|
| `density-acceptance-run-a-2026-10-13.json` | 20 tenants: idle fits, the VM floor (1500 MiB) is breached under parallel calculations; background and combined phases not run; 21st tenant refused above the floor |
| `density-acceptance-run-b-2026-10-13.json` | steps 10 and 15 measured; floor breached at 15; steps 20, 25, 30 `REFUSED_NOT_RUN` |
| `density-acceptance-admission-2026-10-13.json` | real `tenant_budget` decisions with 12-15 live tenants: 14th admitted, 15th refused (`budget`) |
| `tenant-density-2026-10-16.json` | earlier measurement to 20 tenants; 25 and 30 are an extrapolation there, not a measurement |

Explanation: `../TENANT_DENSITY_ACCEPTANCE_2026-10-13.md` (and `../AUDIT_FULL_SERVER_DENSITY_STAND_2026-10-13.md`).
The 25 and 30 tenant steps remain **open**; no result in the next section replaces them.

## Shared PDF slots
| File | What it records |
|---|---|
| `pdf-budget-evidence-2026-10-15.json` | 8 tenants, with and without 2 shared slots, plus SIGKILL recovery (`../PDF_BUDGET_2026-10-15.md`) |
| `pdf-slots-load-*.json` | the load scenario (`scripts/bench_pdf_slots_load.py`): API and background of all N tenants at once, real PDF run per tenant, declared deadline, verdict with the list of failed checks (`../PDF_SLOTS_LOAD_SCENARIO_2026-10-16.md`) |

### Historical (old gates) and attested (current gates)
| Files | Status |
|---|---|
| `pdf-slots-load-{2,3,4}-2026-10-16.json` | **Historical, unchanged.** `PASS` under the gates of 27070ca. Those gates accepted missing kernel peak / API / per-tenant driver data, so they are not evidence for the stricter criteria and are not rewritten. Image: an overlay on a d7f3729 base. |
| `pdf-slots-load-obs-{2,3,4}-2026-10-15.json` | **FAILED, kept as recorded.** First run of the strict gates: three harness faults (a wrong source path, an over-strict mount rule, lock-file read bits), not load faults. |
| `pdf-slots-load-attested-{2,3,4}-2026-10-15.json` | **Current smoke, `PASS`** with the strict gates from 6354bb3 on an image built from the committed sources of d0141b2 (`../PDF_SLOTS_LOAD_SCENARIO_2026-10-16.md`, section "Strict gates"). 2-4 tenants only; not an acceptance of 20/25/30. |

