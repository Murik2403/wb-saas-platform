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
