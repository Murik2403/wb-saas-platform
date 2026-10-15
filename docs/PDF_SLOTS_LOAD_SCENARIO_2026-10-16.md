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

## Результаты локального smoke (2–4 кабинета, слоты = 2)
Акты: `docs/benchmarks/pdf-slots-load-{2,3,4}-2026-10-16.json`. Образ `bench-tenant:pdfload` собран из коммита 4c100a2 (код кабинета с d7f3729 не менялся); сценарий запущен с b50a1fb (каждый акт называет свой HEAD; между ними менялись только сами акты). Все три: `PASS`, дерево чистое, очистка 0/0/0. Продакшен не нагружался.

| | 2 кабинета | 3 кабинета | 4 кабинета |
|---|---:|---:|---:|
| Объявленный срок, с | 120 | 180 | 180 |
| До последнего PDF, с | 111,4 | 145,3 | 150,0 |
| Одновременных PDF (интервалы / замки) | 2 / 2 | 2 / 2 | 2 / 2 |
| Свободная память VM: до → минимум, МиБ | 5795 → 5160 | 5546 → 4780 | 5393 → 4480 |
| /healthz: проб / сбоев / p95, мс | 436 / 0 / 88 | 862 / 0 / 80 | 1183 / 0 / 75 |
| Дашборд p50 / p95 / max, мс; 5xx | 21 / 177 / 2305; 0 | 12 / 77 / 1781; 0 | 12 / 76 / 2322; 0 |
| PDF в запросе: 200 / 503+Retry-After / иное | 4 / 4 / 0 | 11 / 4 / 0 | 10 / 10 / 0 |
| memory.events high/max/oom/oom_kill | 0 | 0 | 0 |
| Пик памяти кабинета, МиБ | 417,5 | 430,7 | 423,8 |
| Throttle CPU, % | 96,1 | 91,2 | 86,0 |
| Admission (реальный `tenant_budget`) | admitted, видит 2, резерв 768 | admitted, 3, 1152 | admitted, 4, 1536 |

Первый прогон (4c100a2) записал `running_tenants=0`: у кабинетов стенда не было метки `wb-saas.slug`, и admission мерился «в пустоту». Исправлено в b50a1fb (метка + вердикт падает, если admission не увидел все N кабинетов); прежние акты заменены новыми.

## Границы
- Это smoke на 2–4 кабинетах, а не приёмка 20/25/30: 20/25/30 не измерялись этим сценарием; прежние отрицательные результаты лежат отдельно (`docs/benchmarks/README.md`).
- Демо-данные, стенд 8 ГиБ / 12 vCPU; CPU упирается в throttle (86–96%), это ограничение кабинетов, а не слотов.
- 4xx дашборда — деловые ответы (409/422), 503 в запросе PDF при занятых слотах — ожидаемое поведение.
- Реальная синхронизация с WB и доставка отчётов не проверялись.

## Strict gates (branch `claude/pdf-slots-observability-20261022`, base d0141b2)
Why: the first version of the scenario (27070ca) could return `PASS` with no kernel `memory.peak`, with no API load, and with the driver data of one of four drivers. The acts `pdf-slots-load-{2,3,4}-2026-10-16.json` are kept unchanged as **historical** results of those gates. The observability draft from review (`deliveries/audit-followup-20261015/claude-slots-observability-DRAFT.patch`) is applied as the first commit; the rest was added on top.

A result is `PASS` only if, in addition to the checks above:

| Gate | What must hold |
|---|---|
| observations | every tenant has dashboard, health and in-request PDF observations, all requested paths seen, a real 200, only 200/409/422 answers; kernel counters (`cur`, `peak`, `high`, `max`, `oom`, `oom_kill`, CPU throttling) present for every tenant before and after, peak > 0; the sampler recorded load-window samples with zero errors and stopped before cleanup; every driver exited 0 with its own result and no worker thread raised |
| jobs | each tenant: a NEW definition with exactly the 21 report metric codes **of the image**, its own `run_id`/`definition_id` returned by the enqueue, that run finished `ok`, exactly one run for that definition, no foreign or duplicate run, no delivery requested |
| image | the image's source commit has the same `tenant-app` and `mh-redesign-lab/tenant-api` git trees as HEAD, and 7 key files (`pdf_budget`, `report_scheduler`, `store`, `pdf_builder`, `catalog`, `background_worker`, `api_server`) hash (SHA-256, CRLF-normalised) to HEAD's blobs inside **every** container |
| topology | per container: the image id, uid 10001, read-only root, `cap_drop ALL`, no `cap_add`, unprivileged, the unchanged 1536 MiB limit, `WB_SAAS_PDF_SLOTS`/`WB_SAAS_PDF_SLOT_DIR`, its own data volume, the one shared `/pdf-slots` volume read-write (the image's own anonymous volume at `/app/tenant-app/data` is the only other volume); API + resident `background_worker` + nginx master alive with the **same PIDs** before and after; no restart, no OOM; the slot directory and every `pdf-slot-NN.lock` have the same device/inode in every tenant, owner 10001, exactly `slots` lock files, directory not accessible to others, no lock file writable by others |
| admission | the real `tenant_budget` decision for the next tenant is taken before the load starts (timestamps compared), saw all N live tenants, and is `admitted`; `refused` is `REFUSED_NOT_RUN` before any load |
| cleanup | every created object (containers incl. the drivers, volumes, network) is recorded by **full ID** when created and removed only by that ID after re-checking its name and run label; nothing unrecorded carrying the run label (reported, left alone); everything that existed before the run still exists (containers also compared by state) |
| harness faults | any exception is a `FAIL` with its reason, never a traceback; cleanup still runs |

`scripts/build_bench_overlay.py` builds the image on a stand without package network: the **committed** `tenant-app/` and `tenant-api` files (via `git archive`) over a base tenant image, refusing a dirty tree, a base without a commit label, or any difference in the dependency/runtime files (`requirements*`, entrypoint, nginx config, the Dockerfile) between the base's revision and HEAD. Limits: files deleted since the base stay in the image; the frontend bundle is the base's (irrelevant to API/PDF load). Used here: base `mh-dr-tenant:fdcc301`, dependencies/runtime files unchanged up to d0141b2.

Tests: `scripts/tests/test_bench_pdf_slots_load.py` (verdict logic) + `test_bench_pdf_slots_attestation.py` (topology, image, jobs, ownership with a fake engine, refusal and fault paths) + `test_build_bench_overlay.py`: 141 passed; 14 mutants of the new gates all killed (`QUEUE`/strict simultaneous profiles are unchanged).

### Results of the attested smokes (current gates, image from the sources of b19413b = d0141b2 app trees)
| | 2 tenants | 3 tenants | 4 tenants |
|---|---:|---:|---:|
| verdict | PASS | PASS | PASS |
| declared deadline / last PDF, s | 120 / 104.1 | 180 / 123.4 | 180 / 169.4 |
| concurrent PDFs (intervals / lock files) | 2 / 2 | 2 / 2 | 2 / 2 |
| VM MemAvailable minimum, MiB | 5012 | 4686 | 4374 |
| kernel events high/max/oom/oom_kill | 0 | 0 | 0 |
| container peak (kernel `memory.peak`) max, MiB | 423.4 | 420.0 | 429.1 |
| `/healthz` probes / failures / p95 ms | 408 / 0 / 86 | 736 / 0 / 71 | 1341 / 0 / 74 |
| admission | admitted, sees 2 | admitted, sees 3 | admitted, sees 4 |
| jobs | 2 x (1 run, 21 metrics) | 3 x | 4 x |
| objects recorded and removed by full ID | 8 / 8 | 10 / 10 | 12 / 12 |
| unrecorded labelled / pre-existing objects missing | 0 / 0 | 0 / 0 | 0 / 0 |

`foreign_changed` lists one pre-existing container (a restart-looping `audittest-traefik` that is not ours); it is recorded and not a failure. Locks are created by the application with mode 0644 inside a 0770 directory owned by 10001: the directory is the barrier, the files are not writable by others.

### Not covered / not claimed
- 20/25/30 remain `NOT_RUN` for this scenario; the strict simultaneous criterion and the negative 25/30 results are unchanged. No queue-aware 20/25/30 run was made.
- Fault execution (unavailable slot directory, SIGKILL of a slot holder, lease expiry/fencing, poison, cancellation, duplicate jobs) is **not** exercised by this scenario; earlier separate evidence for SIGKILL recovery is in `PDF_BUDGET_2026-10-15.md`. Duplicate runs are only detected, not provoked.
- Demo data, no marketplace sync, no deliveries; local 8 GiB / 12 vCPU Docker VM; this is not a fresh-OS test.

