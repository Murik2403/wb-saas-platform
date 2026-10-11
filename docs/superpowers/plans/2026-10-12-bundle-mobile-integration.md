# Bundle and mobile integration implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. This run uses the available collaboration tools for an independent read-only review.

**Goal:** Merge Claude's reviewed frontend split into the audited backend and deploy verified tenant images from committed HEAD.

**Architecture:** Retain backend revision32f67d7 while merging frontend/mobile commits367873c,a7c5f37,c8a0a55 from claude/bundle-mobile-20261012. Rebuild frontend assets over verified live tenant images, preserving API sources/dependencies/data; gateway runtime is unchanged. Pin the new future tenant image in provisioner/gateway configuration.

**Tech Stack:** React19, TypeScript, Vite, Vitest, Capacitor, Docker, Python operator helpers.

**Spec:** Owner's Claude handoff message12October2026; source branch c8a0a55.

## Global constraints

- Preserve current backend fixes, preexisting untrackedfiles and all16lessons.
- No native app publication, store credentials, real WB/Ozon writes or client data changes.
- Telegram remains disabled; UID10001, tenant1536MiB, volumes and aliases preserved.
- Deploy only committed HEAD. Isolated encrypted restore candidate/rollback gates required.
- Retain deep-link waiting, lazy-load failure UI, service-worker asset capacity128.

### Task1: Merge and review

**Files:** mh-redesign-lab/src/*View.tsx,App.tsx,LazyPanel.tsx,cabinet-shared.tsx;public/sw.js;mobileplatformmanifests;existing tests.

- [ ] Verify ancestry, excluded backend paths and merge-tree; merge without foreign edits.
- [ ] Independent review of lazy loading, deep links, cache capacity and platform declarations.
- [ ] npm test, tsc/build, service-worker tests and relevant gateway regression tests.
- [ ] Run Graphify update without staging generated graph churn.

### Task2: Release

**Files:** operator delivery outside repository; source proof pins exactGitarchive and liveimages.

- [ ] Export committed HEAD; verify frontend input SHA and unchangedbackend source trees.
- [ ] Build tenant candidates; rehearse encrypted restore and rollback for all5tenants.
- [ ] Deploy5tenants, preserve backend hash/data/memory/networking;pin future image.
- [ ] Verify publichealth, assetmanifest/chunks/build-id,PWA, browsernavigation+themes+taxdeeplink.
- [ ] Record deployed revision, bundle measurements and remaining native device/signing/store gates.
