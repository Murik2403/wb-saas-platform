# Repository Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the MARKETSHELPER checkout reproducible and safe to build by separating required source from generated artifacts, preserving the current in-flight work, and ensuring CI references only material available from a clean clone.

**Architecture:** Treat `gateway/`, `tenant-app/`, `traefik/`, `scripts/`, `standalone/`, and the deployment manifests as the current Git-backed runtime. Treat `frontend/` and `mh-redesign-lab/` as candidate product source that must be explicitly adopted into Git or split into separately versioned projects before CI depends on them. Keep generated audit, deployment-context, virtual-environment, temporary, archive, and secret material out of Git and Docker build contexts.

**Tech Stack:** Git, Docker Compose, GitHub Actions, FastAPI, Streamlit, Next.js/Vite, PowerShell on Windows.

**Spec:** `AUDIT_2026-09-10.md` sections 2 and 7, plus the current working tree at commit `e726870`.

## Global Constraints

- Do not discard or overwrite the existing 28-file in-flight diff.
- Do not delete untracked files until each top-level path has been classified and any required source has been preserved.
- A clean clone must contain every directory referenced by `.github/workflows/ci.yml`, `docker-compose.yml`, and the Docker build contexts.
- Secrets, live tokens, virtual environments, archives, logs, generated audit folders, and temporary scripts must not enter Git or Docker build contexts.
- Validate with Git status, a clean-clone build/test check, and CI configuration inspection before claiming P0 is resolved.

---

### Task 1: Freeze the current repository evidence

**Files:**
- Read: `.git/`, `.gitignore`, `.dockerignore`, `.github/workflows/ci.yml`, `docker-compose.yml`
- Create: `docs/superpowers/plans/2026-09-10-repository-integrity.md`

**Interfaces:**
- Consumes: current Git index and working tree.
- Produces: a recorded inventory of tracked, modified, untracked, ignored, and referenced paths for the remaining tasks.

- [x] **Step 1: Capture the current branch and status**

Run:

```powershell
git status --short --branch
git diff --stat
git ls-files
```

Expected: the current in-flight modifications remain visible; no files are changed.

- [x] **Step 2: Enumerate CI and Docker path dependencies**

Inspect every `working-directory`, `path`, `context`, `COPY`, volume, and bind mount in `.github/workflows/ci.yml`, `docker-compose.yml`, `gateway/Dockerfile`, and `tenant-app/Dockerfile`.

Expected: each referenced path is recorded as either tracked, untracked source, generated material, or missing.

- [x] **Step 3: Re-run the inventory after each later repository edit**

Run:

```powershell
git status --short --branch
```

Expected: only intentionally changed files differ from the initial evidence.

### Task 2: Classify the untracked source tree without deleting it

**Files:**
- Read: `frontend/`, `mh-redesign-lab/`, `docs/`, `gateway/internal_auth.py`, `gateway/constraints-windows.txt`, `tenant-app/constraints-windows.txt`, `tenant-app/*.py`
- Modify: none until classification is complete

**Interfaces:**
- Consumes: Task 1 path inventory and the existing audit report.
- Produces: an explicit decision for every top-level untracked path: adopt into this repository, split into a separately versioned repository, ignore as generated material, or remove only after preservation approval.

- [x] **Step 1: Identify buildable product roots**

Inspect:

```powershell
Get-ChildItem -LiteralPath frontend -Force
Get-ChildItem -LiteralPath mh-redesign-lab -Force
Get-Content -LiteralPath frontend/package.json -Raw
Get-Content -LiteralPath mh-redesign-lab/package.json -Raw
Get-Content -LiteralPath mh-redesign-lab/tenant-api/pyproject.toml -Raw -ErrorAction SilentlyContinue
```

Expected: the canonical frontend candidate, redesign frontend, and redesign tenant API are named explicitly rather than inferred from directory names.

- [x] **Step 2: Trace whether CI requires those roots**

Inspect `.github/workflows/ci.yml` and record the exact commands that require `frontend/` or `mh-redesign-lab/tenant-api/`.

Expected: every CI step either has its source path available from Git or is marked for removal until the source is adopted.

- [x] **Step 3: Classify required untracked gateway and tenant source**

Review imports and Docker copy behavior for `gateway/internal_auth.py`, `gateway/constraints-windows.txt`, `tenant-app/constraints-windows.txt`, and the untracked tenant service modules.

Expected: runtime-required source is separated from local tooling and generated deployment copies.

### Task 3: Define ignore boundaries before cleanup

**Files:**
- Modify: `.gitignore`
- Modify: `.dockerignore`
- Test: `git check-ignore` results for generated and secret paths

**Interfaces:**
- Consumes: Task 2 classification.
- Produces: ignore rules that keep generated artifacts and secrets out of Git and Docker contexts while leaving required source visible.

- [x] **Step 1: Add narrowly scoped rules for known generated material**

Add rules for the verified artifact classes: `.audit2-*`, `.deploy-context-*`, `tmp-pytest-*`, `.production-recommendations-*`, `.verify-*`, `.serena/`, `.codex/`, root `tmp/`, `*.tar`, `*.tar.gz`, and local virtual environments. Do not ignore `frontend/`, `mh-redesign-lab/`, `docs/`, or runtime Python source.

Expected: generated folders and archives become ignored; product source remains visible as untracked until adopted.

- [x] **Step 2: Exclude generated and secret material from Docker contexts**

Add the same generated classes plus `.env*` exceptions as appropriate, live local configuration, archives, logs, and virtual environments to `.dockerignore` without excluding required build inputs.

Expected: Docker build contexts no longer include the known multi-gigabyte or secret-bearing artifacts.

- [x] **Step 3: Verify ignore behavior**

Run:

```powershell
git check-ignore -v .audit2-backup .deploy-context-56ae174d3f5646f5ac03f71a39f70ea7/tmp-pytest-fifo-fix .tmp-mh-redesign-deploy-56ae-v2.tar.gz
git check-ignore -v frontend mh-redesign-lab gateway/internal_auth.py
```

Expected: generated examples are ignored; required source examples are not ignored.

### Task 4: Adopt required runtime source and repair clean-clone CI

**Files:**
- Add to Git after review: `gateway/internal_auth.py`, required constraints files, required tenant service modules, and the selected frontend/redesign roots
- Modify: `.github/workflows/ci.yml`
- Test: clean-clone CI commands and local build/test commands

**Interfaces:**
- Consumes: Task 2 canonical-source decision and Task 3 ignore boundaries.
- Produces: a Git-backed source set whose CI and Docker references resolve from a clean clone.

- [x] **Step 1: Stage only reviewed required source**

Use `git add --intent-to-add` or explicit path staging for reviewed source paths; do not stage temporary folders, live configuration, archives, logs, or deployment snapshots.

Expected: `git diff --cached --name-status` contains only intentionally adopted source and documentation.

- [x] **Step 2: Make CI conditional only if a product root remains intentionally separate**

If `frontend/` or `mh-redesign-lab/tenant-api/` is not adopted into this repository, remove or relocate the corresponding CI job in `.github/workflows/ci.yml` so the remaining CI is valid on a clean clone. If adopted, keep the job and verify its package/test files are tracked.

Expected: no CI step references a directory unavailable from Git.

- [x] **Step 3: Validate a clean clone without changing the user worktree**

Create a temporary clone from the reviewed commit or staged result outside the working tree, then run the repository’s documented build/test commands from that clone.

Expected: gateway imports resolve, Docker build contexts contain required files, and CI-referenced tests do not fail due to missing directories.

### Task 5: Resolve secrets and cleanup only after source adoption

**Files:**
- Review: `standalone/config.yaml`, `.env.example`, backup paths, `.gitignore`, `.dockerignore`
- Remove only after explicit classification: generated folders, archives, logs, temporary scripts, deployment snapshots, local virtual environments

**Interfaces:**
- Consumes: Tasks 2–4 classification and clean-clone validation.
- Produces: a workspace with no live tokens in synchronizable project paths and no large generated payloads in build contexts.

- [x] **Step 1: Revoke or rotate any token found in local plaintext configuration**

Before deleting or moving `standalone/config.yaml`, inspect only whether it contains a live credential; do not print the credential value. Rotate/revoke it through the provider before cleanup.

Expected: the exposed credential is no longer valid, and no secret value is copied into Git, logs, or the plan.

- [ ] **Step 2: Remove only verified disposable artifacts**

After recording the classification and verifying no required source exists only inside them, remove generated audit folders, deployment-context snapshots, temporary test trees, archives, logs, and local virtual environments from the workspace.

Expected: product source remains intact; the workspace size drops substantially.

- [ ] **Step 3: Run the final repository-integrity checks**

Run:

```powershell
git status --short --branch
git diff --check
git ls-files | Select-String -Pattern '(^|/)(\.env|config\.yaml)$|\.tar(\.gz)?$|(^|/)\.venv(/|$)'
```

Expected: no accidental secret/archive/virtual-environment files are tracked; whitespace and line-ending issues are visible for follow-up.
