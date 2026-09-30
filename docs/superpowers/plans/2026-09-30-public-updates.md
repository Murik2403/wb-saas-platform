# Public Updates Implementation Plan

**Goal:** Publish a discoverable journal of verified releases without customer data or unreleased promises.

**Architecture:** Add `/updates` to the existing server-rendered marketing router, so sitemap and llms discovery use the existing common list. Reuse shared layout and link the journal from its footer.

**Tech Stack:** FastAPI, Jinja, existing Gateway SEO tests.

**Spec:** FULL_PRODUCT_MARKET_AUDIT_2026-09-23.md section 7 item 9.

## Constraints

- Only already deployed capabilities in dated entries; no customer numbers, secrets, competitor comparisons or promised future delivery.
- Label manual historical imports as preliminary and distinguish API collection from accounting verification.

## Task

- [ ] Add route tuple in `gateway/marketing_routes.py`, `gateway/templates/marketing_updates.html`, footer link in `gateway/templates/base.html`.
- [ ] Extend existing public SEO test to verify route/discoverability; existing dynamic public-page tests check response, metadata/canonical/sitemap.
- [ ] Independently review template against release evidence; run Gateway suite.
- [ ] Build immutable Gateway from checked existing exact image and source overlay; verify all changed source bytes and unchanged dependencies.
- [ ] Rehearse fresh encrypted control-plane restoration on new and rollback images, deploy, verify live public pages and journal.
