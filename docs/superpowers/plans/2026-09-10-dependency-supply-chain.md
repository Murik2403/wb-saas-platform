# Dependency Supply Chain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every deployed Python dependency reproducible and hash-verified, continuously audit Python and Node dependency graphs, scan built images, and keep dependency update workflows active.

**Architecture:** Keep concise human-maintained `requirements.in` files as each Python component's declared policy and generate committed, fully pinned `requirements.lock` files with `pip-compile --generate-hashes`. Dockerfiles and CI install only lock files. Existing `package-lock.json` remains the canonical Node lock. CI fails on known high/critical vulnerabilities, and Dependabot proposes bounded updates for each manifest ecosystem.

**Tech Stack:** Python 3.12, pip-tools 7.6.1, pip-audit, npm 11/Node 24, GitHub Actions, Trivy, Dependabot.

**Spec:** `AUDIT_2026-09-10.md` P1-3 and section 7 step 5.

## Global Constraints

- Preserve the currently passing gateway, provisioner, tenant-app, frontend, and redesign builds.
- Commit only reproducible source, lock files, CI configuration, and documentation; never commit virtual environments, audit caches, credentials, or scanner databases.
- Python locks must use exact versions and hashes and be generated under Python 3.12.
- Production Dockerfiles and CI must install `requirements.lock`, never resolve from a lower-bound-only input file.
- Node installs remain `npm ci`, which consumes existing committed `package-lock.json` files.
- CI vulnerability gates use `pip-audit -r <lock>` and `npm audit --audit-level=high`; scanner results at high/critical severity fail the job.

---

### Task 1: Establish declared Python dependency policy and deterministic locks

**Files:**
- Create: `gateway/requirements.in`, `gateway/requirements.lock`
- Create: `tenant-app/requirements.in`, `tenant-app/requirements.lock`
- Create: `provisioner/requirements.in`, `provisioner/requirements.lock`
- Modify: `gateway/requirements.txt`, `tenant-app/requirements.txt`, `provisioner/requirements.txt`

**Interfaces:**
- Consumes: existing imported package requirements and Python 3.12 resolver output.
- Produces: exact hash-checked install inputs consumed by Docker and CI.

- [x] **Step 1: Split declared requirements from generated locks**

Move each component's direct dependencies and version ceilings into `requirements.in`. Keep each existing `requirements.txt` as a short compatibility pointer containing exactly `-r requirements.lock`, so existing operator commands remain valid but deterministic.

- [x] **Step 2: Add ceilings for every direct runtime and test dependency**

Use these direct constraints before compilation:

```text
# gateway/requirements.in
fastapi>=0.141,<0.142
uvicorn[standard]>=0.52,<0.53
jinja2>=3.1,<4
python-multipart>=0.0.9,<1
docker>=7.0,<8
requests>=2.31,<3
pytest>=9.0.3,<10
psycopg[binary]>=3.1,<4
testcontainers[postgres]>=4,<5
```

```text
# provisioner/requirements.in
fastapi>=0.141,<0.142
uvicorn[standard]>=0.52,<0.53
docker>=7.0,<8
```

Keep tenant-app's tested FastAPI/Uvicorn/Pytest ceilings and add `<next-major>` ceilings to every remaining direct dependency.

- [x] **Step 3: Generate lock files with hashes**

Run from repository root with Python 3.12 and pip-tools 7.6.1:

```powershell
python -m piptools compile --resolver=backtracking --generate-hashes --allow-unsafe --strip-extras --output-file gateway/requirements.lock gateway/requirements.in
python -m piptools compile --resolver=backtracking --generate-hashes --allow-unsafe --strip-extras --output-file tenant-app/requirements.lock tenant-app/requirements.in
python -m piptools compile --resolver=backtracking --generate-hashes --allow-unsafe --strip-extras --output-file provisioner/requirements.lock provisioner/requirements.in
```

Expected: every resolved package has an exact `==` version and one or more SHA-256 hashes.

- [x] **Step 4: Verify lock integrity without resolving unpinned inputs**

Create temporary Python 3.12 virtual environments outside the repository and run:

```powershell
python -m pip install --require-hashes -r gateway/requirements.lock
python -m pip install --require-hashes -r tenant-app/requirements.lock
python -m pip install --require-hashes -r provisioner/requirements.lock
```

Expected: all three installs succeed with hash checking enabled.

### Task 2: Make image builds and CI consume lock files

**Files:**
- Modify: `gateway/Dockerfile`, `tenant-app/Dockerfile`, `provisioner/Dockerfile`, `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: `requirements.lock` from Task 1.
- Produces: identical dependency sets in development tests, CI, and built runtime images.

- [x] **Step 1: Update Dockerfile installation commands**

Replace each `COPY requirements.txt` and install invocation with `COPY requirements.lock` and:

```dockerfile
RUN python -m pip install --no-cache-dir 'pip>=26.2,<27' \
    && pip install --no-cache-dir --require-hashes -r requirements.lock
```

- [x] **Step 2: Update CI Python installation steps**

Install each component's lock file with `--require-hashes`, retaining only an explicit unpinned `pytest` installation for provisioner's test runner until it receives a separate development lock.

- [x] **Step 3: Add audit gates**

Install `pip-audit`, run it once per Python lock, and run `npm audit --audit-level=high` after each existing `npm ci`. Save machine-readable Python audit reports as workflow artifacts only when a scan fails.

- [x] **Step 4: Build and scan images**

Add a security job that builds gateway, provisioner, and tenant-app images with commit-derived local tags, then runs `aquasecurity/trivy-action` against each image with `severity: HIGH,CRITICAL` and `exit-code: 1`.

### Task 3: Configure sustainable dependency updates and document the workflow

**Files:**
- Create: `.github/dependabot.yml`
- Modify: `DEPLOY.md`

**Interfaces:**
- Consumes: manifest locations and lock files from Tasks 1–2.
- Produces: weekly update proposals and a repeatable manual lock-refresh command.

- [x] **Step 1: Enable weekly Dependabot updates**

Configure `pip` for `/gateway`, `/tenant-app`, and `/provisioner`; `npm` for `/frontend` and `/mh-redesign-lab`; `docker` for each directory containing a Dockerfile; and `github-actions` for `/`.

- [x] **Step 2: Document lock refresh and review**

Add one deployment-maintenance section that requires a Python 3.12 lock regeneration, `pip-audit`, component tests, Docker builds, and review of Dependabot pull requests before merging an upgrade.

### Task 4: Validate the supply-chain controls

**Files:**
- Test: all lock files, Dockerfiles, workflow YAML, existing component tests and builds

- [x] **Step 1: Run pip-audit and npm audit against resolved graphs**

Expected: no high or critical known vulnerability is silently ignored; any finding is fixed by an explicit dependency update or recorded as an operational exception outside source control.

- [x] **Step 2: Rebuild all three images**

Run `docker compose build gateway provisioner` and `docker build -t wb-dashboard-tenant:dependency-verify ./tenant-app`.

- [x] **Step 3: Run component verification**

Run gateway/provisioner/tenant tests and both frontend builds; then run `git diff --check` and inspect the staged diff for only dependency-policy work.

- [ ] **Step 4: Commit**

```powershell
git add gateway tenant-app provisioner .github DEPLOY.md docs/superpowers/plans/2026-09-10-dependency-supply-chain.md
git commit -m "build: lock and audit dependencies"
```
