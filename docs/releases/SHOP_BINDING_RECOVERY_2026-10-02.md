# Primary shop binding recovery — production 2026-10-02

Deployed tenant source: `7e67b39a97a93b9c1560b7a0d02c108ad5d3e9aa`.
Image: `sha256:08c6ac1c425f7dd414d5208b29f71a562200777f3ba8e9fd1a5634fb158e2d76`.

Root cause: apiHeaders(true) supplies lower-case content-type; bootstrap added Content-Type as a second object key. Browser Headers merged these into `application/json, application/json`. Production logged POST primary-binding 422. A regression using the actual API Input model/FastAPI parser in an isolated candidate reproduces 422 with this value and accepts a single application/json (200), without accessing live data or WB. Normalized Headers.set removes the duplicate.

Failure recovery: identity verification still fails closed. When binding fails, an invalidation event unmounts the stale cabinet; a boundary error shows the refusal and offers fresh session validation. No financial panel remains mounted with missing global context. Foreign bootstrap responses remain rejected; no fallback shop is selected.

Verification: 421/421 frontend tests, TypeScript and Vite build. Clean Git archive repeated the same suite and build; source/image proof verified 253 runtime files and 9 asset files, immutable base bytes, candidate import and tax gate. Graphify AST updated. Manifest SHA256 `f24420390c02ee3f9c09249c4ef91d4577301a8aea4dad2ec844feef3b532f1f`.

demo and mvvorokov deployed sequentially after verified encrypted backups. Four business API probes per tenant passed. Public gateway/demo/mvvorokov healthz returned status ok. Tenant limits preserved at 1536 MiB. Provisioner pinned to this exact tenant image, retaining its own existing image. Gateway and Traefik did not require changes.

Rollback: wb-tenant-demo-rollback-20261002-094719; wb-tenant-mvvorokov-rollback-20261002-094810; wb-saas-provisioner-1-rollback-1790934548038310770.

Live authenticated mvvorokov: Finance P&L and monthly report loaded; stock loaded; navigation Stock → Finance works. Screenshot saved in private deliverables binding-7e67b39/finance-live.jpg. Actual primary warehouse binding remains an explicit owner action: no live binding or WB write was performed during acceptance. Failed binding/retry path is covered by frontend integration tests.

Server disk free approximately 710 MiB (99% used); no customer backups or rollback containers were removed.
