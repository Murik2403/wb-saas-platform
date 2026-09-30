# Atomic shop bridge: bounded core acceptance

The prepared child snapshot can now join canonical warehouse operations and an immutable request receipt in one main SQLite writer transaction. Supplied allocator connections require a pre-existing transaction and never initialize, commit or roll back their caller. Child `prepare()` preserves the stored blob/revision and carries frozen verified seller identity.

Lost-success recovery rechecks active shop, seller identity and ACL, then reads receipt plus current context from one explicit read snapshot. Original applied revision remains distinct from current revision. Exact original UUID/base/body recovers after later writes; changed payload or revision conflicts. Foreign-shop reservations and injected actor/connection overrides are rejected.

Meaningful evidence: injected failure after canonical allocation rolls back reservation, physical documents, snapshot and receipt; stale concurrent writers yield one commit; revoked ACL denies replay; append-only receipt UPDATE/DELETE/INSERT OR REPLACE fails. The real isolated worker cost preview returns changed private data while the stored shop snapshot remains identical.

Independent review accepted this core only. Request selector, verified primary binding, route registry, generic child dispatch, physical-sync restrictions, common manufacturing targeting, post-dispatch actual returns/transfers and frontend integration remain separate required gates. No public feature or deployment is claimed by this document. Existing legacy `execute()` remains available until its public callers move to the verified adapter.

After shared physical facts are written, rollback requires verified database restoration with newer facts preserved. Reverting only the container image is unsafe.
