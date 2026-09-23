# Manufacturing costs UI implementation plan

**Goal:** Deliver the production-cost forms against the agreed manufacturing backend; root agent integrates the panel and router.

**Architecture:** Isolated React component and namespaced CSS. API base and tenant headers follow data-source.ts. No demo or successful-save fallback on a failed backend response.

**Scope:** Create ManufacturingCosts.tsx, ManufacturingCosts.css and behavioral tests only. Parent owns App.tsx, data-source.ts exports and API router; inventory agent owns backend accounting. No deployment or commit by this agent.

- [x] Implement GET /api/v1/manufacturing-costs with products, materials, material_layers, boms, equipment, piecework, defects and releases.
- [x] Implement POST forms /boms, /equipment, /piecework, /defects and /releases using the agreed function arguments. BOM material rows use catalogue units; equipment usage units follow equipment. Actual piecework must be registered before the immutable release.
- [x] Preserve decimal text input including commas; validate monetary precision, quantities and selection before sending. Use idempotency event keys for piecework/defects and source_key for release.
- [x] Block simultaneous writes. Keep a write success separate from a failed refresh; retain entered values on a write failure.
- [x] Test Russian decimal values in request payload, failed save with retry, duplicate click prevention, empty/failed loading and material units.
- [x] Run focused Vitest and TypeScript; report integration dependencies to root.

Verification: ManufacturingCosts.test.tsx 8/8 passing, TypeScript noEmit passing. Added real-router tests/test_manufacturing_routes.py with temporary SQLite: 12/12 passing. No real tenant data or WB operations were used. Browser visual and production checks remain with parent integration, together with the single coordinated graphify update.
