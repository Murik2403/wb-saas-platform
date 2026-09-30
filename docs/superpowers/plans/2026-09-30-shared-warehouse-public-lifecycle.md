# Stage 5: public shared warehouse lifecycle and financial projection

Status: architecture proposal for parent and independent review; no implementation
claimed. Stage4 is e09b250. Work remains in the isolated shop-operations worktree.

## Physical authority and identity

Keep the existing primary SQLite canonical FIFO layers as the only physical stock.
Primary binding requires a server-side test of the already stored WB credential;
only its verified seller identity may register shop0. Duplicate seller identities
across primary/secondary are rejected. No user-supplied seller hash is trusted.
Existing legacy layers are neither duplicated nor automatically revalued.
After remote primary verification, re-read the stored credential before and during
the binding transaction; a credential rotated during verification invalidates the
old result. Secondary catalog/financial proof freezes snapshot identity+revision
and rechecks both inside the main commit transaction. Test rotation between proof
and commit. Credentials/hashes are not returned or logged.

Expose `/api/v1/shared-warehouse` as an explicit central tenant resource. Each
operation names its target shop and frozen seller identity; selected header and
target must agree. Every target has a fresh ACL check before read/write/replay.
Primary existing access migrates explicitly, secondary remains owner-only until
granted. Manager/warehouse roles cannot grant access or register seller identities.

Canonical items require a real primary catalog nmID/chrtID; shop mappings require
an exact selected catalog product/variant snapshot. Mapping creation is explicit,
immutable and validated against seller identity. Same nmID in another shop is not
evidence of the same physical SKU. Unmapped/ambiguous variants remain unavailable
for physical allocation and retain unknown/estimated financial cost.

## Public contract

Owner setup: verify primary identity, register canonical item, map external SKU.
Read: target mappings, physical/free/quarantine/reserved stock and owned inbound/WB
balances, reservation documents, exact sale provenance and available return layers.
Preview: reservation, sale and return plan; no writes until explicit confirmation.
Confirm: reserve, dispatch (`commit`), arrival (`receipt`), cancel uncommitted
reservation (`release`), sale, original-document return and owned quarantine/release.
Pydantic strict bounded payloads prohibit actor,role,connection overrides. Verified
gateway actor is journaled. Source document+UUID+optimistic revision freeze payload.
Changed UUID payload conflicts; exact replay rechecks current ACL and returns the
original result without a second physical movement. No external WB writes occur.

Transitions reuse `shared_warehouse` supplied-connection functions within one
BEGIN IMMEDIATE transaction. Reservation/source shop must equal target. Dispatch
releases only its own reservation before moving FIFO stock; all other reservations
and free incident holds remain protected. Arrival and sale preserve original layer
cost/monetary residuals. A return restores only its original consumed layers. Partial
multi-layer returns require explicit per-layer plan plus client attestation/proof;
they are not called WB-verified serial matching. Unknown origins never synthesize
stock. Return quarantine is created in the same transaction as restoration.

## Financial projection

Add an immutable explicit binding between a canonical sale/return document and an
actual selected-shop financial source row, with frozen seller identity, source row
ID, exact mapped variant, event kind, date and quantity. Verify all these against
the stored selected snapshot; no matching by nmID alone, by current cost, or by
text source_ref alone. A changed/ambiguous financial row stays preliminary.
Bindings cannot count one financial source row twice or reuse a physical sale for
multiple source rows beyond its immutable quantity/cost. Cancellation/returns use
their original canonical allocation amount and cumulative residual policy.
Actual variant proof must come from selected snapshot chrtID/barcode or an
unambiguous verified sales source joined by srid, never from the UI's chosen variant.
An nmID-only financial row with multiple variants remains ambiguous. Apply the same
projection to primary0 P&L and exports: managed SKU legacy sales remain physically
blocked, while canonical exact bindings replace primary estimates without another
consumption or duplicated revenue. Test primary+secondary sameSKU consolidation.

Prepare a read-only projection envelope for child calculations: exact source-row
costs and lineage only; never physical FIFO tables/layers. Child cost reconciliation
uses verified projection where all identity/date/quantity checks match, otherwise
its explicit estimate/unknown path. Stored raw WB revenue remains authoritative;
central costs replace, not add to, the child's existing COGS contribution. Root
consolidation sums each verified seller once and never central+private duplicates.

## Shared manufacturing target

The physical production workspace remains global. Explicitly assign a target shop
to planned manufacturing documents as immutable metadata after shop ACL validation;
new finished stock is still global canonical FIFO, not a copied child layer. A shop
receives ownership only through the explicit allocator reservation/dispatch chain.
Existing execution tasks/batch dates and facts remain intact. Manufacturing cannot
consume quantities protected by another shop's reservation or quarantine.
Mapped intermediate SKU consumption currently hits the intentional legacy mover
guard. Add a typed central manufacturing-consumption entry point using caller's
transaction, explicit manufacturing source document and free-ready FIFO only.
Journal consumed quantity/amount immutably and include it in ready-basis conservation
alongside reservations/ownership, so remaining monetary basis cannot silently drift.
Reversal restores original layers and amounts only after downstream-dependency,
reservation and hold checks; no bypass flag opens legacy generic movers.

## UI coordination

The frontend agent owns all React edits after the backend contract is frozen. Show
the selected/target shop and central warehouse distinction, real variant mapping,
preview→confirm, original source documents, return allocation/proof, quarantine,
preliminary financial coverage, request conflicts and exact replay recovery.
Continuous secondary scheduled reports remain a separate worker-dispatch dependency
from stage4's supported manual archive; do not silently enable unsupported schedules.

## Acceptance, in order

1. Two verified shops map explicitly to one canonical variant; concurrent reserve
   cannot overdraw; conflicting seller/variant/ACL/revoked replay fail closed.
2. Reserve→dispatch→arrival→sale preserves quantities, pipeline and monetary basis.
   Another shop cannot consume owned units; foreign/free holds remain effective.
3. Partial multi-layer return rejects missing proof; explicit selection restores
   original amount; damaged return quarantines atomically; repeated UUID cannot
   change disposition or create a second unit. Fragmented0.335 costs conserve money.
4. Financial source binding exactness, duplicate prevention, unmapped/unknown facts,
   changed source-row version, profit/exports/consolidation provenance all verified.
5. Private two-shop clone through Nginx8501 under exactly1.5GiB proves authenticated
   full public lifecycle, UI context, PDF archive, simultaneous finance readers,
   original database integrity and encrypted backup/restore of every new table.

No production mutations in this subagent. Deployment belongs to parent after full
gates and independent review. Downgrade after new central writes requires restoring
the verified compatible snapshot while preserving subsequent evidence, not simply
restarting an old image. Whole audit remains open until all five scenarios and UI
evidence pass; a route registry alone does not close this block.


## Trusted primary membership migration boundary

The existing gateway ForwardAuth resolves the requested tenant slug from the
forwarded host, verifies the signed session and explicit tenant membership, then
forwards X-Tenant-Account and X-Tenant-Role. The tenant API is behind this trusted
network boundary; direct internet access to its port is not an authentication
contract. Body/header values supplied outside ForwardAuth are not trusted proof.
Server TENANT_SLUG must be configured before primary migration. Known current
roles and a bounded forwarded account are required, including for primary owner.
A first explicit primary warehouse access may append a migration journal with
server tenant slug, account, current source_role, verified seller identity and
aware UTC time. Only primary0 inherits existing membership this way; secondary
shops remain owner-only until an explicit ACL grant. Current role restricts every
write and is rechecked before replay; a historic can_write grant never elevates a
currently read-only role. Account membership revocation is enforced upstream on
every request. No new unchecked user-controlled tenant/seller header is introduced.
