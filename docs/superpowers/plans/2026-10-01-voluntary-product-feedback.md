# Voluntary product feedback collector

Goal: collect F201 actual self-reported reconciliation time and optional refusal reason without claiming interviews.
Architecture: authenticated owner Gateway form uses existing session and CSRF; additive portable account-owned table, upsert by scenario/period, own deletion, aggregated admin-only results excluding service accounts. Cabinet Settings links to the private form, no new tenant bridge or scoped credential exposure.
Constraints: enum fields only, explicit past period, optional observed integer minutes, no PII/free text, no messages, no billing or customer accounting edits, no public publication consent.
- [x] Add migration and validated storage/summary.
- [x] Add private native form and session/owner/CSRF/rate boundaries.
- [x] Add Settings link component; root integrates App mount.
- [x] Verify new HTTP/DB/UI tests and existing admin gate, commit.

Verification: 44 Gateway focused tests (7 collector + existing growth/admin/integration), 1 Settings link component test, TypeScript/Vite build passed. No production writes or outreach. Runtime panel is intentionally not mounted in App by this isolated change; integrating root adds import ProductFeedbackLink and <ProductFeedbackLink /> to Settings.

The collection is explicit self-reporting for a user-selected past period, not independently measured time, observed interviews, a real public case, or conversion/churn evidence. A non-refusal response can submit only observed minutes. Missing minutes stay NULL, observed zero stays 0. Every response has an explicit scenario and period; summaries count responses and unique organizations separately. Service exclusions reuse growth_sources and administrator/demo classification. Account deletion cascades feedback, and owner can delete own visible responses.
