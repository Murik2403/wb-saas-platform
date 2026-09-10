# Password Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reject compromised passwords during browser registration/reset and migrate account hashes to Argon2id without invalidating PBKDF2 credentials.

**Architecture:** A gateway adapter computes SHA-1 locally and sends only the first five uppercase characters to the HIBP Pwned Passwords range endpoint after full-form submit. Accounts recognize legacy PBKDF2 and Argon2 PHC records; a successful legacy login rewrites only that account with Argon2id.

**Tech Stack:** Python 3.12, argon2-cffi, urllib, SQLite/PostgreSQL-compatible account queries, pytest.

**Spec:** `C:\Users\mvvor\Downloads\audittracker.html` (P3-7).

## Global Constraints

- Never send a plaintext password or full SHA-1 digest outside the process.
- Use HTTPS, `Add-Padding: true`, bounded timeout, and discard the HIBP response after matching.
- Check only complete submitted forms; a HIBP failure blocks registration/reset but not login and must not consume a reset token.
- Retain PBKDF2 verification; do not mass-rewrite accounts or force resets.
- Generate Argon2id with 19 MiB memory, time cost 2, parallelism 1. Keep `password_salt` empty for PHC rows.

---

### Task 1: HIBP range adapter

**Files:**
- Create: `gateway/password_breach.py`
- Create: `gateway/tests/test_password_breach.py`

**Interfaces:**
- Produces: `pwned_password_count(password: str, *, opener: Callable = urlopen) -> int`.
- Raises: `PasswordBreachCheckError` for a remote or malformed response.

- [x] **Step 1: Write failing tests**

Use a fake opener to assert that the URL has only a five-character prefix, `Add-Padding` is true, a matching suffix returns its count, an absent suffix returns zero, and an opener failure exposes no password in the error.

- [x] **Step 2: Run the red test**

Run: `python -m pytest gateway/tests/test_password_breach.py -q`

Expected: import failure before implementation.

- [x] **Step 3: Implement the adapter**

Hash UTF-8 password text with SHA-1, fetch `https://api.pwnedpasswords.com/range/{prefix}`, parse only `SUFFIX:COUNT` lines, and translate transport/parse failures into the application exception.

- [x] **Step 4: Run the green test**

Run: `python -m pytest gateway/tests/test_password_breach.py -q`

Expected: PASS.

### Task 2: Argon2id and lazy PBKDF2 upgrade

**Files:**
- Modify: `gateway/requirements.in`
- Modify: `gateway/requirements.lock`
- Modify: `gateway/logic/accounts.py`
- Modify: `gateway/tests/test_accounts.py`

**Interfaces:**
- Produces: Argon2 PHC string in `password_hash`, empty `password_salt` for new/updated accounts.
- Consumes: existing hexadecimal PBKDF2 hash and salt rows.

- [x] **Step 1: Write failing migration tests**

Assert new hashes start with `$argon2id$`; a legacy PBKDF2 fixture still verifies; a successful legacy login rewrites its row; and a wrong legacy password does not rewrite it.

- [x] **Step 2: Run the red test**

Run: `python -m pytest gateway/tests/test_accounts.py -q`

Expected: new hash is PBKDF2 and lazy migration does not occur.

- [x] **Step 3: Pin, lock, and implement dispatch**

Add `argon2-cffi>=25,<26`, regenerate the Python 3.12 hash lock, configure `PasswordHasher(type=Type.ID, memory_cost=19456, time_cost=2, parallelism=1)`, dispatch on `$argon2id$`, and rehash only after a verified legacy login.

- [x] **Step 4: Verify account migration**

Run: `python -m pytest gateway/tests/test_accounts.py -q`

Expected: PASS.

### Task 3: Browser policy and reset safety

**Files:**
- Modify: `gateway/app.py`
- Create: `gateway/tests/test_password_policy_routes.py`
- Modify: `gateway/config.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `DEPLOY.md`

**Interfaces:**
- Consumes: `pwned_password_count()`.
- Produces: 400 for a compromised password; 503 for an unavailable check; an intact reset token after rejection.

- [x] **Step 1: Write failing route tests**

Patch the adapter to return a non-zero count and assert registration rejects before account creation. Patch it to raise and assert reset gives 503 while the token remains usable. Patch it to zero and assert the normal flow delegates.

- [x] **Step 2: Run the red test**

Run: `python -m pytest gateway/tests/test_password_policy_routes.py -q`

Expected: routes do not call the adapter.

- [x] **Step 3: Implement route policy**

Add the configurable timeout and route-level exception handling. Preserve generic messages, rate limiting, CSRF, and reset-token single-use semantics. Production compose must not provide an opt-out.

- [x] **Step 4: Verify the gateway**

Run: `docker build -t wb-saas-gateway:latest gateway`, then run all gateway tests from `/repo/gateway` and `docker compose config`.

### Task 4: Audit and commit

**Files:**
- Modify: all files above
- Create: `docs/superpowers/plans/2026-09-10-password-hardening.md`

- [x] **Step 1: Run the dependency audit and whitespace check**

Run: `python -m pip_audit -r gateway/requirements.lock` and `git diff --check`.

- [x] **Step 2: Commit only password-hardening files**

Run: `git add` with the explicit changed paths, then `git commit -m "security: screen passwords and migrate to argon2id"`.
