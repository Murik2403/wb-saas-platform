# Shared warehouse lifecycle core

This extends the schema/allocator dependency. It remains an internal core: no public routes, UI, verified-credential binding or secondary snapshot bridge is activated. The full multi-shop audit item remains open.

## Implemented facts

Received shop/reservation ownership can be sold partially without consuming another shop's units. Every sale records its original layers, original rate, allocated monetary amount and source document. Changed or stale confirmations, reverse chronology, duplicate sale/return quantities and unauthorized access fail closed.

A return restores only a known original sale allocation. A single-layer or all-remaining return is unambiguous. A partial multi-layer return requires explicit per-layer quantities and a bounded document/reason attestation. The immutable receipt labels this `user_confirmed`, not API-verified serial traceability. Unknown sale documents do not manufacture stock.

Layer-level ready monetary bases preserve rounding across reservations, cancellation releases and two shops. Owned remaining amounts preserve fragmented sale costs; cumulative original sale amounts preserve partial return costs and the exact final residual. Examples tested: layer 6 × 0.335 = 2.01 distributes 1.01 + 1.00 across two reservations; sale 3 × 0.335 = 1.01, three returns restore .34 + .33 + .34 = 1.01. Re-sale retains 1.01.

Owned quarantine is a subset of existing shop/reservation ownership, with immutable layer/location/proof identity. It does not add another physical claim. A shop's sale availability subtracts its own holds; another shop remains unaffected. Inbound receipt cannot move held units until documented release. A damaged return restores ownership and creates quarantine inside one transaction. Injected hold failure rolls back physical quantity, money, return counters and the new document.

Before-dispatch cancellation releases the original reserve and exact money. After dispatch, no operation silently synthesizes ready stock or releases quarantine; a separate confirmed physical return/transfer workflow is still required.

## Verification

There are 37 shared allocator/lifecycle regressions, including nonempty stage-one monetary/schema migration, SQL replace guards, concurrent sales/returns, explicit allocation evidence, residual conservation, cross-feature planner/quarantine and an initialization check that cannot commit the caller's pending business transaction. Independent reviewer accepted the core after an isolated 102-test focused run. Fresh final gates with unique UUID temporary paths: 448 tenant tests plus 8 subtests, 378 API tests passed. Graphify updated; diff whitespace validation passed.

An invalid parallel run collided in the repository's default `tmp_pytest` directory and is excluded as acceptance evidence. Subsequent final runs use distinct UUID paths outside the worktree under `release-rehearsal-tools/test-artifacts`.

No frontend or production acceptance is claimed. Full route classification, owner/ACL binding, prepared child snapshot plus central fact atomic commit, shared manufacturing context, after-dispatch return/transfer and explicit selected-shop UI remain required before public activation.

Rollback after any new allocation/ownership/money/quarantine write requires a verified database restore, preserving/reconciling newer facts first. Reverting only a container can bypass the new stock protections.
