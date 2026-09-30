# Shop request bridge implementation plan

**Goal:** Select a verified shop explicitly, preserve its ACL and commit its prepared financial snapshot and canonical physical facts atomically.

**Architecture:** The existing isolated child process produces an uncommitted SQLite snapshot. The main tenant opens one BEGIN IMMEDIATE, validates identity/revision/ACL, applies only typed canonical operations, stores the child snapshot and immutable request receipt, then commits once. No global DB_PATH switch, no independent child physical FIFO, no primary fallback.

- [ ] Add supplied-transaction support to allocator entry points; initialization must precede the caller transaction, supplied connection never commits/rolls back implicitly.
- [ ] Split child preparation from snapshot persistence; commit helper accepts only active matching seller identity and optimistic revision.
- [ ] Add immutable bridge receipts with UUID, method/path/query/body identity and base revision fingerprint. Replays recheck ACL and identity before returning old response. Changed payload/key conflict; distinct concurrent stale writes rejected.
- [ ] Guard legacy shop-id endpoints independently of selector; filter listing/consolidation by ACL.
- [ ] Establish explicit route-scope registry and independent review; deny unknown and external-write secondary routes.
- [ ] Add request dispatcher to private child ASGI context for permitted local routes, disable independent physical sync, project only canonical attributable costs. Shared production/warehouse stays tenant-wide with explicit target shop.
- [ ] Add verified primary seller binding and migrations preserving existing primary memberships; never derive verified identity from an unchecked token.
- [ ] Implement after-dispatch actual return/transfer and shared manufacturing authority through canonical allocator.
- [ ] Independent review, isolated full gates, parent integration/deploy. This plan does not claim any public adapter exists before acceptance.

## Frontend contract (frozen names)

GET `/api/v1/shop-context/session`: `{selected_shop_id:0,role,shops:[{id,name,verified_identity,active,snapshot_revision,can_write,allowed_actions}],route_scopes:[{method,path,scope}]}`. `verified_identity` is opaque hash or null for unbound primary; `snapshot_revision` is integer for secondary, null for primary. No credentials in catalog.

Request selector `X-MH-Shop-ID`: strict nonnegative decimal integer; absent means legacy primary 0. Secondary mutations require UUID `X-MH-Request-Key` and integer `X-MH-Shop-Revision`.

Response headers: `X-MH-Shop-ID`, `X-MH-Shop-Identity` (absent unbound primary), `X-MH-Shop-Revision` (secondary only), `X-MH-Shop-Fingerprint` (secondary only), `X-MH-Resource-Scope` (`shop_local` or `tenant_shared`). Existing array/file response bodies remain unchanged. Optional dictionary `shop_context` additive only. Unknown/invalid/unauthorized/stale selection returns 400/403/409 with `{detail:{code,message}}`, never primary data.

Route scopes are `shop_local`, `tenant_shared`, `forbidden`. Global cash, backups, memberships and manufacturing are tenant_shared; global production uses explicit target_shop_id rather than independent child manufacturing copies. Secondary external WB writes are forbidden initially. Every actual method/template is classified explicitly before activation; new unknown routes are fail-closed.

## Required evidence

Inject failure after physical allocation before snapshot/receipt: none persist. Retry same request: no second movement. Retry after ACL revoke: denied. Changed snapshot/base revision with same UUID: conflict. Concurrent stale shop writes: exactly one commit. Foreign reservation/source documents: denied before mutation. Primary existing tests unchanged. Full pytest unique external UUID basetemp only.

Rollback after new wallet/shared-stock facts requires verified pre-release database restore with newer facts preserved; old image alone is not a downgrade contract.
