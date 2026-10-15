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
