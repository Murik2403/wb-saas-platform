# SEO Machine Lite Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a small, evidence-backed SEO operating loop for MARKETSHELPER without mass publishing or new paid SEO infrastructure.

**Architecture:** Keep the existing FastAPI/Jinja marketing routes and current analytics/funnel scripts. Add durable content planning and read-only audits as repository documents/scripts; only publish content after human fact-checking and route/SEO tests pass.

**Tech Stack:** Python 3.12, FastAPI/Jinja gateway, existing pytest suites, Graphify knowledge graph, Yandex Webmaster/Search Console evidence, Markdown content registry.

**Spec:** `docs/marketing/SEO_SEBESTOIMOST_WB_REVIEW.md` and `C:/Users/mvvor/Downloads/seo-mashina-pro.pdf`.

## Global Constraints

- Use real demand evidence; do not invent search volumes, rankings, customer claims, or traffic forecasts.
- One search intent has one owning URL; do not create synonym pages for the same cluster.
- Do not mass-publish AI text; every article needs fact-checking, sources, a concrete example, and a product-accurate CTA.
- Keep paid tools and advertising disabled unless explicitly launched by the owner.
- Read-only audits must not write production data or create real registrations/payments.

### Task 1: Build the semantic and URL registry

**Files:**
- Create: `docs/marketing/SEO_CLUSTER_REGISTRY.md`
- Reference: `docs/marketing/SEO_SEBESTOIMOST_WB_REVIEW.md`, `gateway/marketing_routes.py`, `gateway/app.py`

- [x] Record each initial cluster, intent, owning URL, supporting URLs, evidence source, status, and next review date.
- [x] Include the current money pages (`/sebestoimost-wb`, `/calculator`, `/pricing`) and existing guides without inventing new routes.
- [x] Mark unknown demand as `unverified` rather than filling guessed volumes.
- [x] Review links and route names against the live route tests.

### Task 2: Prepare a ten-topic content queue

**Files:**
- Create: `docs/marketing/SEO_CONTENT_QUEUE.md`
- Reference: `docs/content/01-sebestoimost-wildberries.md`, `docs/content/03-fifo-vs-srednyaya.md`, `docs/content/04-sverka-ostatkov-wb.md`, `docs/content/05-nezavershennoe-proizvodstvo.md`

- [x] Rank ten topics by commercial proximity and evidence readiness.
- [x] Assign each topic to one existing URL or explicitly mark it as a future route requiring review.
- [x] Add the factual source set, intended CTA, anti-duplication note, and acceptance gate for every topic.
- [x] Keep the first publishing rhythm to one manually reviewed article per week.

### Task 3: Add a read-only SEO health audit

**Files:**
- Create: `scripts/audit_public_seo.py`
- Test: `gateway/tests/test_public_seo_audit.py`

- [x] Fetch the configured public routes without authentication.
- [x] Check status, canonical, title, description, H1, FAQ JSON-LD, sitemap membership, robots directives, and internal links.
- [x] Emit JSON with `pass`, `warn`, and `fail`; never write production data.
- [x] Add tests using local fixtures for a passing page, a missing canonical, and a broken internal link.

### Task 4: Verify the first content slice

**Files:**
- Modify only the selected existing guide/template after review.
- Test: existing marketing route and SEO suites.

- [ ] Select one topic from the queue with verified demand and complete facts.
- [ ] Fact-check every number and product claim against current code and public documentation.
- [ ] Add contextual links to the owning money page and one supporting guide.
- [ ] Run route, SEO, link, and render checks before any production deployment.

### Task 5: Establish the operating cadence

**Files:**
- Create: `docs/marketing/SEO_OPERATING_CADENCE.md`

- [x] Define weekly demand review, weekly content review, post-deploy SEO smoke, and monthly refresh review.
- [x] Use the existing `scripts/report_growth_funnel.py` for business outcomes; do not substitute visits for activation or payment.
- [x] Define stop conditions for duplicate topics, unsupported claims, broken routes, and missing evidence.

**Completion gate:** Tasks 1-3 are complete with reviewable files and passing tests; Task 4 is complete only after human fact-checking and a verified deploy; Task 5 records the recurring process. Advertising remains a separate owner decision.
