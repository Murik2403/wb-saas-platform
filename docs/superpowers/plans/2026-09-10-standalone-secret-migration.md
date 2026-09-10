# Standalone Secret Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the standalone marketplace credentials outside the repository while retaining an explicit, testable configuration override.

**Architecture:** `standalone/run_agents.py` will resolve a path in this order: `WB_SAAS_STANDALONE_CONFIG_PATH`, then `~/.wb-saas-secrets/standalone-config.yaml`. The live ignored `standalone/config.yaml` moves to that user-private default path; no credential is copied into tracked files.

**Tech Stack:** Python 3.12, PyYAML, pytest, PowerShell/Windows ACLs.

**Spec:** `C:\Users\mvvor\Downloads\audittracker.html` (P3-3: plaintext WB token outside a synchronised working copy).

## Global Constraints

- Never print, commit, or copy the credential value.
- Keep `standalone/config.example.yaml` as a secret-free template.
- Allow an explicit `WB_SAAS_STANDALONE_CONFIG_PATH` for managed secret stores.
- Move the existing file; do not duplicate it.

---

### Task 1: Resolve standalone credentials outside the checkout

**Files:**
- Modify: `standalone/run_agents.py:10-37`
- Modify: `standalone/config.example.yaml:1-2`
- Test: `standalone/tests/test_run_agents_config.py`

**Interfaces:**
- Produces: `config_path() -> pathlib.Path`.
- Consumes: optional `WB_SAAS_STANDALONE_CONFIG_PATH` environment variable.

- [x] **Step 1: Write the failing path-resolution tests**

```python
def test_config_path_defaults_to_user_private_directory(monkeypatch):
    monkeypatch.delenv("WB_SAAS_STANDALONE_CONFIG_PATH", raising=False)
    assert run_agents.config_path() == Path.home() / ".wb-saas-secrets" / "standalone-config.yaml"

def test_config_path_honors_explicit_override(monkeypatch, tmp_path):
    target = tmp_path / "secret.yaml"
    monkeypatch.setenv("WB_SAAS_STANDALONE_CONFIG_PATH", str(target))
    assert run_agents.config_path() == target
```

- [x] **Step 2: Run the test to verify it fails**

Run: `python -m pytest standalone/tests/test_run_agents_config.py -q`

Expected: FAIL because `config_path` does not exist.

- [x] **Step 3: Implement path resolution and update template guidance**

```python
def config_path() -> Path:
    override = os.environ.get("WB_SAAS_STANDALONE_CONFIG_PATH")
    return Path(override).expanduser() if override else Path.home() / ".wb-saas-secrets" / "standalone-config.yaml"
```

- [x] **Step 4: Run the targeted tests**

Run: `python -m pytest standalone/tests/test_run_agents_config.py -q`

Expected: PASS.

### Task 2: Move the live local secret without exposing it

**Files:**
- Move: ignored `standalone/config.yaml` → `%USERPROFILE%/.wb-saas-secrets/standalone-config.yaml`

**Interfaces:**
- Consumes: default returned by `config_path()`.
- Produces: one private local secret file outside the checkout.

- [x] **Step 1: Confirm the source is ignored and the destination is absent**

Run: `git check-ignore standalone/config.yaml` and `Test-Path $env:USERPROFILE/.wb-saas-secrets/standalone-config.yaml`

Expected: source ignored; destination absent.

- [x] **Step 2: Create a user-private directory, move the file, and validate existence**

```powershell
New-Item -ItemType Directory -Path "$env:USERPROFILE/.wb-saas-secrets" -Force
Move-Item -LiteralPath standalone/config.yaml -Destination "$env:USERPROFILE/.wb-saas-secrets/standalone-config.yaml"
```

- [x] **Step 3: Commit code, template, tests, and this plan**

```bash
git add standalone/run_agents.py standalone/config.example.yaml standalone/tests/test_run_agents_config.py docs/superpowers/plans/2026-09-10-standalone-secret-migration.md
git commit -m "security: move standalone secrets outside checkout"
```
