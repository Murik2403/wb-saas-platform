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

## Fail-closed gates (branch `claude/pdf-slots-failclosed-20261023`, base 430fc6f)
The review of 98e3d5f required five changes; all are in `scripts/`, `scripts/tests/` and `docs/` only.

1. **Ownership before mutation.** Every name (network, slot volume, each tenant's container and data volume) is checked free (`inspect` answers "No such ...") before anything is created. A created object is verified by name and by this run's label **before** it is mounted, chowned or seeded: `docker volume create` is idempotent and would otherwise return somebody else's volume. A lost create answer is reconciled by the pre-reserved name (ours is adopted and removed, someone else's is reported and never touched, an engine that does not answer leaves the state UNKNOWN).
2. **Cleanup fails closed.** `docker rm` is followed by a second authoritative `inspect`; only "No such ..." counts as gone. A timeout, a refused connection or an unparseable answer is **UNKNOWN**, which fails the act, as does any leftover, any recorded object that vanished earlier, any object whose name or label no longer matches, any unrecorded object carrying the run label, and any name collision. An inventory that cannot be read raises instead of looking empty.
3. **Admission and the memory floor per tenant.** The real `tenant_budget` (budget + host floor, counting tenants still being created through its pending claims) is asked before each tenant's first object is created; the VM's `MemAvailable` is read through a throwaway container so this works before the first tenant exists. A refusal stops creation there (`REFUSED_NOT_RUN`, with how many tenants had been created) and the cleanup of what was created is still verified. The act records every decision with its time and the tenant's creation time.
4. **Mandatory, typed evidence.** Lock directory and every `pdf-slot-NN.lock`: positive integer device and inode, integer owner and mode (a missing field is no longer `None == None` or a default mode 0), exactly the expected file names. The limits (memory, reservation, CPU, pids, OOM score) are compared in the inspection **before and after** the load. Source hashes: every tenant, exactly the attested files, including `reports/metrics.py` and `db/financial_fifo_reconciliation.py` (the d014 memory fix). The raw per-tenant driver buckets, kernel counters, both inspections, process lists, lock identities and hashes are kept in a `.raw.json` sidecar (sha256 in the act) so the gates can be replayed.
5. **Overlay pinned.** `build_bench_overlay.py` builds `FROM` the inspected immutable image ID with `--pull=false --network none` (classic builder: BuildKit would try to resolve the ID as a registry reference), then refuses the result unless it starts with exactly the base's layers and keeps its entrypoint, user, env, volumes and healthcheck.

**Foreign lifecycle.** Pre-existing containers are read twice before the run. A container whose state moved between the readings, or that the engine reports as `restarting`, is listed as `foreign_unstable_before_the_run` (not hidden); every other pre-existing container must still exist and be in the same state, otherwise the act fails. This proves preservation for the stable containers and states explicitly that the churning one is outside the claim. The earlier acts' `foreign_changed` boundary is unchanged and not relabelled.

**Tests.** `test_bench_pdf_failclosed.py` runs the harness against a fake engine with fault injection (failing `rm`, unreachable daemon, lost create answers, a foreign volume appearing between the name check and the create, a restarting container). Together with the scenario and overlay tests: all of `scripts/tests` passes; 20 mutants of the new gates are all killed. Found and fixed on the way: an early `return` on a refusal skipped the cleanup verdict, so a refused run with an unverifiable cleanup could have been reported as a clean refusal.

**Smoke 2-4** on an image built from the committed sources of this branch (`bench-tenant:fc-b22d871`, base `mh-dr-tenant:fdcc301`, 42 inherited layers + 27 overlay layers); per-act status in `benchmarks/README.md`. 3 and 4 tenants: `PASS` (139.4 s and 150.9 s against 180 s; two PDFs at once at most; zero `memory.events`; zero failed `/healthz`; every created object confirmed gone; no pre-existing object missing or changed besides the listed restart loop). 2 tenants: one `FAIL` (120.8 s against the declared 120.0 s) and one `PASS` (117.6 s), both kept. The declared deadline is not adjusted after the fact; a margin of 90 s instead of 60 s would be a decision for later runs.

Not done: 20/25/30 (`NOT_RUN`, no large run, no production touched); fault *execution* in real containers (SIGKILL of a slot holder, lease expiry, poison, cancellation); fresh-OS recovery stays with Codex.

## Inventory fix (review of f54e656, P2)
`inventory()` accepted an exit-0 `docker inspect` answer that was empty, partial or malformed and recorded a smaller (even empty) baseline of pre-existing objects. Now every listed ID must come back exactly once as a typed row (full 64-hex ID, boolean running, known status, numeric restart count, five fields), no duplicate, unexpected or missing ID; listings of containers, volumes and networks reject duplicates and short IDs. Anything else is an `ObservationError`: the run refuses to start on an unreadable baseline and a cleanup report without a readable post-run inventory is UNKNOWN. The review's counterexample is a regression test; 5 mutants of the parser are all killed.

## Fault scenarios (`scripts/bench_pdf_faults.py`, branch `claude/pdf-faults-20261024`, base 97b0d5f)
Real local tenants (3 or 4, demo data, one shared slot volume, `WB_SAAS_PDF_SLOTS=2`), the real resident worker, real PDF builds, the same admission gate per tenant and the same fail-closed ownership/cleanup as the load scenario. The act is `PASS` only if every check of every scenario holds.

| Scenario | Fault | What is checked (all held in both runs) |
|---|---|---|
| S1 | `docker kill` (SIGKILL) of the tenant that builds while another tenant waits for a slot | exit code 137; the slot is free / the queued tenant starts within 0.6 s (limit 15 s); the restarted tenant's run is requeued and finishes ok on attempt 2 with a real PDF; the other runs stay on attempt 1; at most 2 builds overlap; a duplicate enqueue returns the same run in every tenant |
| S2 | a run whose owner stopped renewing (`dead-worker`, expired lease) in a LIVE tenant | the resident worker takes it over without a restart (attempt 2); the dead owner's late `finish_run` and `heartbeat` are both rejected and change nothing, while the new attempt is running and after it finished; ok with a real PDF |
| S3 | the worker dies on every attempt: SIGKILL, restart, lease expiry, three times | the run is closed with the "interrupted 3 times" error at attempt 3, no PDF recorded, and stays closed (never requeued) through six further worker ticks; another tenant's job completes ok meanwhile |
| S4 | cancel (see below) of a queued run and of a running build | the queued run vanishes and never builds; the running build's definition is deleted: the row disappears, nothing is resurrected when the build ends, the container and worker survive (same start time), the slot is released, and the tenant's next job completes ok on attempt 1 |

Findings and limits to read with the table:
- **There is no cancel API for a report run.** The cancel equivalent tested is `delete_definition`, which also deletes the definition's runs. An in-flight build is not interrupted by it (it finishes and its result is discarded); a PDF file it wrote is not tracked by any row (orphan file, not measured here).
- **The kernel frees a SIGKILLed holder's slot at once, but the run stays `running` until its lease expires** (300 s); only then does the live worker requeue it, and a restart does not shorten that (startup recovery only takes expired leases). The scenarios therefore expire the dead owner's lease by an explicit, recorded injection into the tenant's own demo database instead of waiting 300 s; the 300 s wait itself was not exercised.
- S3's poison cycle uses real SIGKILLs and the real recovery code, with the same lease-expiry injection after each kill.
- Demo data, 8 GiB / 12 vCPU local stand; no marketplace sync, no deliveries; 20/25/30 not exercised; no production touched; fresh-OS recovery stays with Codex. Not covered: an unavailable or read-only slot directory.

## Fault scenarios: review of 32f192c (three P2) and the reruns
1. **Memory and OOM observations are mandatory and typed.** The sampler records every read with its time; a failed read is an error (UNKNOWN), never a skipped sample. Each of the four scenarios needs at least 2 samples and no gap above 10 s between its start, its samples and its end; the OOM flag of every tenant is read with its exit status after each scenario and at the end, and an unavailable, empty or non-boolean answer is an error. Memory is read through a throwaway container, not through a tenant (S1 and S3 kill tenants).
2. **S1 kills only after seeing a waiting contender.** Every slot must be busy by the slot files AND by run statuses, and at least one tenant must be observed `queued`; this is re-read at the kill instant (a contender that just started means no kill and a failed check). After the kill that same tenant must be observed `running` within 15 s. If the precondition never holds, nothing is killed and the act fails.
3. **S3 has a neighbour building at every kill.** Each cycle first starts a build in another tenant and waits until it is running; only then is the poison run claimed (cycle 1) or its dead lease expired so that it is requeued and re-claimed (cycles 2-3). The neighbour's status is read at the kill instant and must be `running` each time, and all three neighbour builds must finish `ok` on attempt 1 with a real PDF. The third dead lease is expired after the last kill, and the run must be closed with the "interrupted 3 times" error and stay closed.

The stricter gates found two faults of my own harness on their first real run: in S3 the neighbour was awaited after the poison run was claimed, so its 30 s build ended on its own before the kill (the kill interrupted nothing and the next expiry found no run), and the sampler read through tenant 1, which S1 had just killed. Both acts (v2, 3 and 4 tenants) stay as recorded `FAIL`; the corrected harness passes on 3 and 4 tenants (v3, see `benchmarks/README.md`).

Tests: a small simulated fleet drives the real S1/S3 functions (the kill is absent when nothing waits, when the slot files disagree with the statuses, when the contender started at the last moment; the neighbour must already build before each poison attempt; an idle neighbour or a late kill fails) and the real `scenario()` observation code against the review's unreadable-memory and unreadable-OOM fixtures. 656 passed in `scripts/tests`; the mutants of the new conditions are all killed.

Limits unchanged: the 300 s lease is shortened by a recorded injection; the cancel equivalent is deleting the definition; in S4 "same container start time" is not a measured worker PID; orphan PDF files are not measured; an unavailable or read-only slot directory is not exercised; the fault acts have no raw sidecar; 20/25/30 not exercised; production untouched.

