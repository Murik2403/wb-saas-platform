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

### Fail-closed gates (branch `claude/pdf-slots-failclosed-20261023`, base 430fc6f)
| Files | Status |
|---|---|
| `pdf-slots-load-failclosed-{2,3,4}-2026-10-15.json` (+ `.raw.json`) | **FAILED, kept as recorded.** First run of the fail-closed gates. All three failed on one cleanup rule: a pre-existing restart-looping container (`audittest-traefik-1`) changed state during the run and the 3 s stability window did not see it churn. Not a load fault. |
| `pdf-slots-load-strict-2-2026-10-15.json` | **FAILED, kept as recorded.** The 2-tenant run finished its last PDF at 120.8 s against the declared 120.0 s: a real deadline miss (the run is CPU-throttled), not a gate fault. The declared deadline was not changed afterwards. |
| `pdf-slots-load-strict-2b-2026-10-15.json` | Repeat of the same 2-tenant scenario: `PASS`, last PDF at 117.6 s. Both results are evidence of the same variance (90-121 s over the 2-tenant runs); neither replaces the other. |
| `pdf-slots-load-strict-{3,4}-2026-10-15.json` (+ `.raw.json`) | `PASS` under the fail-closed gates (139.4 s and 150.9 s against 180 s). |

Raw evidence (per-tenant driver buckets, kernel counters before/after, both inspections, process lists, lock identities, hashes) is in the `.raw.json` sidecar named, with its sha256, in each act.

### PDF fault scenarios (branch `claude/pdf-faults-20261024`, base 97b0d5f)
| File | Status |
|---|---|
| `pdf-faults-3-2026-10-16.json` | **FAILED, kept as recorded.** Harness fault before any fault was injected: `op() got multiple values for argument 'name'`; fixed with a regression test. |
| `pdf-faults-3b-2026-10-16.json` | `PASS`: 3 tenants, all four scenarios, 33 of 33 checks. |
| `pdf-faults-4-2026-10-16.json` | `PASS`: 4 tenants, 33 of 33 checks. |
Description and limits: `../PDF_SLOTS_LOAD_SCENARIO_2026-10-16.md`, section "Fault scenarios".

### PDF fault scenarios, review fixes (branch `claude/pdf-faults2-20261025`, base c55bc3b)
| File | Status |
|---|---|
| `pdf-faults-3b-2026-10-16.json`, `pdf-faults-4-2026-10-16.json` | Earlier `PASS` acts (33 of 33). Their gates did not require a complete memory/OOM observation, a waiting contender before the S1 kill, or a neighbour build running through the S3 kills; kept as recorded, not upgraded. |
| `pdf-faults-v2-3-2026-10-16.json` | **FAILED, kept as recorded.** First run of the stricter gates. Two harness faults found by them: S3 waited for the neighbour after the poison run was claimed, so the 30 s build ended by itself and the second kill hit an idle tenant (nothing to expire); the memory sampler read through tenant 1, which S1 had just killed (2 failed reads = UNKNOWN). |
| `pdf-faults-v2-4-2026-10-16.json` | **FAILED, kept as recorded.** The same S3 ordering fault on 4 tenants (the third kill hit an already finished build) and 2 failed memory reads. |
| `pdf-faults-v3-3-2026-10-16.json` | `PASS` with the fixed harness (db86d64): 38 of 38 checks, 0 memory read errors, every scenario covered by samples with gaps <= 2.5 s, typed OOM flags for every tenant after every scenario and at the end. |
| `pdf-faults-v3-4-2026-10-16.json` | `PASS`, same gates, 4 tenants. |

### Queue priority and request deadline (branch `claude/pdf-fairness-20261026`, base 49be22f; `../PDF_FAIRNESS_2026-10-16.md`)
| File | Status |
|---|---|
| `pdf-slots-pressure-before-6-2026-10-16.json` (+ `.raw.json`) | **FAILED, kept as recorded.** The starvation of the real 20/25/30 run reproduced locally with the previous code: 6 tenants, a request for a PDF every second per tenant, declared deadline 240 s: 3 of 6 background PDFs finished while requests got 31 builds. |
| `pdf-slots-pressure-after-6-2026-10-16.json` (+ `.raw.json`) | `PASS` with the fix, same scenario and criteria: all 6 background PDFs in 218.5 s (deadline 240 s); requests: 8 served, 1264 refused with 503 + Retry-After, none otherwise. |
| `pdf-slots-load-fair-4-2026-10-16.json` | `PASS`, default pressure, 4 tenants, 139.6 s (deadline 180 s). |
| `pdf-faults-v4-3-2026-10-16.json` | `PASS`, fault scenarios on the fixed code (S1 re-reads slots, victim and contender at the kill instant). |
The real 20/25/30 acts of the Docker24 stand (FAIL, 7/20, 8/25, 7/30) stay as delivered; they were not rerun here.

