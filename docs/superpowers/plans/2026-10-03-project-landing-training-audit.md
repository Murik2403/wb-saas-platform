# Project, Landing and Training Audit Implementation Plan

> **For agentic workers:** Execute the three delegated work packages below with independent evidence and verification before release.

**Goal:** Audit the project against the public landing page, publish grounded descriptions of shipped features, and deliver five new completed video lessons.

**Architecture:** Keep gateway marketing templates and the approved training catalog separate from tenant runtime. Reuse the immutable training-media mount and reviewed manifest. Preserve the deployed tenant revision and existing lessons.

**Tech Stack:** FastAPI/Jinja, React/TypeScript, Python tests, HyperFrames/MP4/VTT, Docker.

**Spec:** User request of 2026-10-03: full project audit, landing parity, new video lessons, agents and applicable skills, deployment after validation.

## Global constraints

- Preserve Claude's tabs integration `52c0d1a` and uncommitted TrueStats review.
- No public competitor referrals or 1C links. Feature claims must match available behavior and data sources.
- No private financial values, credentials or live WB write operations in videos or audits.
- Do not label unavailable fields as confirmed zeros or manual calculations as automatic accounting.
- No paid security/LLM scans without a task-specific spending mandate.
- Publish real reviewed MP4/VTT assets, retain existing immutable lessons, verify HTTP/range playback.
- Commit release source; encrypted gateway backup and rollback; keep provisioner alias `provisioner` and its tenant-image pin.

## Work package 1 — project and claim audit

Owner: project_landing_audit. Output: `docs/marketing/PROJECT_LANDING_PARITY_2026-10-03.md`.

- [x] Build evidence matrix for all cabinet/public/admin/integration sections.
- [x] Identify recent shipped features, omissions and unsupported claims.
- [x] Distinguish code/test evidence, deployed UI and real-data acceptance.
- [ ] Root checks runtime health, release identity, public SEO/security headers and media integrity.

## Work package 2 — landing and catalog

Owner: landing_refresh. Files: gateway templates, training catalog and focused gateway tests.

- [x] Update feature cards, onboarding explanation, CTA/FAQ and new training links.
- [ ] Keep public claims grounded and mobile/light/dark presentation coherent.
- [x] Add lesson metadata from video owner and preserve old lessons; media acceptance remains a separate gate.
- [x] Run relevant gateway tests and render actual public pages for visual acceptance. Local desktop light/dark accepted; responsive viewport control did not apply, so mobile visual acceptance is not claimed.

## Work package 3 — completed new video lessons

Owner: training_videos. Files: reproducible media sources/scripts, external reviewed media pack.

- [x] Author five lessons: taxes, advanced products, ads analytics, FBS and the manual cost journal. Show navigation/tabs within the lessons.
- [x] Render MP4 plus Russian subtitles and poster; verify full decoding, duration and visual samples. All five: 120s, 1920×1080, 24fps; six reviewed samples each.
- [x] Hand off exact slugs, metadata and immutable hashes to landing owner/root. Combined pack preserves the 11 published lessons exactly.

## Work package 4 — acceptance and release

Owner: root.

- [x] Inspect every delegated change, fix material issues and update Graphify. 9505 nodes, 24808 edges; no paid labeling.
- [x] Verify gateway suite, public pages, media manifest and clean release source. Gateway 432 passed/2 skipped/203 subtests; frontend 483 passed and tsc/build; actual pack 16 MP4+16 VTT hash/range checks passed.
- [ ] Publish reviewed assets, deploy gateway from committed HEAD with backup/rollback.
- [ ] Check live landing/training URLs, media bytes/range requests, health and screenshots.
- [ ] Record receipt and remaining real-data/product limitations; only then report completion.
