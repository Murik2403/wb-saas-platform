# Selected-shop public dispatch: stage 4

Scope: frozen seller identity, explicit URL/header shop ACL, route classification,
isolated selected-credential child, transactional local snapshot writes and exact
UUID replay. The primary API and database path remain unchanged. A secondary
unknown or forbidden route never falls back to primary.

## Authority

Production/WIP/workcenters/physical inventory/cash remain central tenant resources.
Financial and marketplace readers and cost settings execute in a private selected
shop snapshot. All WB writes and foreign-host requests are blocked in that child.
No child FIFO layer movement or synthetic stock is permitted. Old private FIFO
facts are not described as central verified cost; missing central projection stays
preliminary. Seller binding is explicit; no automatic nmID identity merge.

The URL target on legacy shop report/sync/cost endpoints is checked independently
of the selector. Reports recheck ACL, active identity and snapshot revision after
the child completes. Consolidation checks every included target after its read.
Cost history is selected-shop-local; production norm history is central. Secondary
history requires exactly one supported kind. Static metadata includes query scopes.

## Bounded execution

One private process executes at a time. Reads have eight total queue slots, and
queue plus child share a monotonic 90-second deadline (shorter explicit budgets
retain their total bound). Writes fail while another worker runs. Five-second
exact-context GET cache holds at most eight entries/eight MiB; seller revision,
account, role, dates and envelope are in the key. ACL and revision checks still
occur around cached reads. Bodies stream with a one-MiB bound before buffering;
child responses are bounded to four MiB; snapshots retain existing 128-MiB bound.

Nginx named routing extends only strict positive selected-shop requests to 100s;
primary generic API remains 60s. Frontend needs an aligned selected-shop timeout.
Existing explicit long sync routes retain their separate bound.

## Manual reports

Quick/generate requests claim exactly their returned run ID, generate one private
PDF and archive immutable bytes plus SHA256 in the same snapshot transaction.
Archive limits: four MiB/file, 32 MiB total. Restored filenames use run IDs only.
Foreign filesystem paths are never captured. JSON strips private file paths.
Delivery is never attempted by private workers. Continuous scheduled secondary
report execution is not provided by this stage; it requires a selected-shop job
dispatcher. Snapshot and receipt roll back together if archive generation fails.

## Required evidence

Full tenant and API suites use unique external basetemp. Independent review covers
route classes, ACL after worker, replay, history scope, credentials, body bounds,
sync quality, report ID/restore/hash/isolation and normal parallel GETs. Native
private clone acceptance must use Nginx8501 under the real1.5-GiB limit, including
parallel finance readers and delayed child timeout; Uvicorn-only evidence does not
close that release gate. No production writes are authorized to this subagent.

## Remaining stage 5

Public canonical mapping/reservation/dispatch/arrival/sale/return/quarantine APIs,
explicit shared manufacturing shop target and canonical financial projection are
still required. This stage does not close the whole shop operations audit. New
central movements cannot be rolled back merely by starting an old image: restore
the verified compatible database snapshot while preserving newer evidence first.
