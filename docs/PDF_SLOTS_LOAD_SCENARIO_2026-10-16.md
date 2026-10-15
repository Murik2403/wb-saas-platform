# Load scenario: shared PDF slots = 2 with API and background work of all tenants at once

Branch `claude/pdf-slots-load-20261020`, base `d7f3729`. Only benchmark scripts, their tests and docs changed; no application, provisioner or deployment code. Production is not touched (local Docker only).

## What it checks
`scripts/bench_pdf_slots_load.py`. N real tenant containers (nginx + API + resident `background_worker`, demo data) mount **one** shared volume at `/pdf-slots` with `WB_SAAS_PDF_SLOTS=2`. In one window:

- the API of every tenant takes dashboard traffic (3 requests in flight per tenant), `/healthz` probes every 0.5 s and a request for a PDF built **inside the request** every 20 s (it may answer `503` + `Retry-After` while the slots are busy);
- every tenant gets a **real queued PDF run** (all report metrics: pandas + matplotlib), built by its resident background worker under the shared slots.

The deadline is **declared before the run**: `ceil(N / slots) * pdf_seconds + margin` (defaults 60 s and 60 s: N=4, slots=2 gives 180 s). The result is `PASS` only if all hold:

| Check | Condition |
|---|---|
| deadline | every run `ok`, last one finished within the declared time, each a real PDF (`%PDF-` header, size >= 10 kB) |
| slots | at most 2 PDFs at once (run intervals **and** sampled lock files); when N >= 2 both slots were actually used, otherwise the run proved nothing |
| health | no failed `/healthz` probe, p95 within `--health-p95-ms` (3000) |
| api | no 5xx or no-answer on dashboard endpoints; the in-request PDF answers 200, or 503 **with** `Retry-After`, never anything else |
| kernel memory | `memory.events` high / max / oom / oom_kill unchanged, no container OOM flag, no restart |
| stand | VM `MemAvailable` stayed at or above `--min-vm-available-mib` (1500). Below it is **a stand limit = FAIL**, never a pass |
| admission | the real `tenant_budget` decision for one more tenant is recorded with its numbers |
| cleanup | no container, volume or network created by the run remains |

If the VM cannot hold the load above the floor before it starts, the result is `REFUSED_NOT_RUN` with the numbers. Every result carries the git HEAD it ran from; a dirty tree is refused (`--allow-dirty` marks it).

## What it does not prove
- It is not the 20/25/30 tenant acceptance: those steps stay open and their earlier negative results are kept unchanged (`benchmarks/README.md`).
- Real marketplace sync and delivery are not exercised (no credentials, no network); `sync`/`agents` are no-ops in demo data.
- The stand is a local 8 GiB / 12 vCPU Docker VM, not the 16 GiB / 8 CPU production geometry.

## Commands
```bash
# image: the tenant runtime with the code under test (build the same way as for bench_pdf_budget.py)
python scripts/bench_pdf_slots_load.py --image bench-tenant:pdfload --tenants 4 --slots 2 --out docs/benchmarks/pdf-slots-load-4.json
python -m pytest scripts/tests/test_bench_pdf_slots_load.py -q    # verdict logic, 34 tests, no Docker
```
