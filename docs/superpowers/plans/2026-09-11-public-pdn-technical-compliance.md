# Public PDN Technical Compliance Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stop public pages and tenant dashboards from contacting Google Fonts, and make the public site's strictly-necessary-cookie behavior visible to visitors.

**Architecture:** Keep the existing CSS design system but use OS-local font stacks, so neither the gateway nor tenant image fetches a foreign font provider. Add a small self-contained cookie notice in the shared gateway base template; it records only the visitor's local display preference and never gates essential authentication cookies.

**Tech Stack:** FastAPI, Jinja templates, vanilla browser JavaScript, Streamlit, Python unittest.

**Spec:** https://pdn.normaltech.ru/scan/scan_ade81b78dfcd4faaa7 and user request in this Codex conversation.

## Global Constraints

- Do not load fonts, analytics, widgets, or other optional third-party resources before a user choice.
- Do not claim a legal retention period, processor, or cross-border-transfer status without an operations-approved data map.
- Preserve the existing required, unchecked registration consent control.
- Do not modify unrelated dirty worktree files.

---

### Task 1: Remove external font requests

**Files:**
- Modify: `gateway/templates/base.html:7-9,31-32`
- Modify: `tenant-app/app.py:72-74,89-90`
- Test: `gateway/tests/test_public_privacy_assets.py`

**Interfaces:**
- Consumes: existing CSS custom properties `--font-display` and `--font-mono`.
- Produces: templates that render with local system fonts and make no request to `fonts.googleapis.com` or `fonts.gstatic.com`.

- [x] **Step 1: Write the failing regression test**

```python
def test_public_templates_do_not_reference_google_fonts(self):
    for path in (GATEWAY_BASE, TENANT_APP):
        source = path.read_text(encoding="utf-8")
        self.assertNotIn("fonts.googleapis.com", source)
        self.assertNotIn("fonts.gstatic.com", source)
```

- [x] **Step 2: Run the test to verify it fails**

Run: `python -m unittest gateway.tests.test_public_privacy_assets -v`

Expected: FAIL because both source files currently reference Google Fonts.

- [x] **Step 3: Replace remote font imports with OS-local CSS stacks**

```css
--font-display: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Arial, sans-serif;
--font-mono: ui-monospace, "SFMono-Regular", Consolas, "Liberation Mono", monospace;
```

- [x] **Step 4: Run the regression test**

Run: `python -m unittest gateway.tests.test_public_privacy_assets.PublicPrivacyAssetsTests.test_public_templates_do_not_reference_google_fonts -v`

Expected: PASS.

### Task 2: Add a strictly-necessary-cookie notice

**Files:**
- Modify: `gateway/templates/base.html:after body content, before closing body`
- Modify: `gateway/tests/test_public_privacy_assets.py`

**Interfaces:**
- Consumes: `/privacy#cookies` as the existing cookie-information destination.
- Produces: `#cookie-notice`, accept and decline buttons, and `marketshelper_cookie_notice` local-storage key.

- [x] **Step 1: Extend the failing template test**

```python
def test_base_template_has_cookie_notice_with_a_real_decline_path(self):
    source = GATEWAY_BASE.read_text(encoding="utf-8")
    self.assertIn('id="cookie-notice"', source)
    self.assertIn('id="cookie-accept"', source)
    self.assertIn('id="cookie-decline"', source)
    self.assertIn('marketshelper_cookie_notice', source)
```

- [x] **Step 2: Run the test to verify it fails**

Run: `python -m unittest gateway.tests.test_public_privacy_assets.PublicPrivacyAssetsTests.test_base_template_has_cookie_notice_with_a_real_decline_path -v`

Expected: FAIL because the base template has no notice yet.

- [x] **Step 3: Implement the notice without optional tracking**

```html
<section id="cookie-notice" role="dialog" aria-label="Использование cookie">
  <p>Мы используем только необходимые cookie для работы и защиты сервиса.</p>
  <a href="/privacy#cookies">Подробнее</a>
  <button id="cookie-decline" type="button">Отклонить необязательные</button>
  <button id="cookie-accept" type="button">Понятно</button>
</section>
```

The accompanying script must only hide the notice and write the selected value to `localStorage`; it must not create a tracking cookie or load any third-party script.

- [x] **Step 4: Run the template test suite**

Run: `python -m unittest gateway.tests.test_public_privacy_assets -v`

Expected: PASS.

### Task 3: Verify rendered pages and update project graph

**Files:**
- Modify: `graphify-out/` generated metadata only

**Interfaces:**
- Consumes: passing unit tests and the rendered Jinja base template.
- Produces: evidence that no Google Font endpoint remains in the modified sources and that public pages expose both cookie choices.

- [x] **Step 1: Run gateway regression tests**

Run: `python -m unittest discover -s gateway/tests -v`

Expected: PASS with no failures.

- [x] **Step 2: Inspect remaining external font references**

Run: `rg -n -i "fonts\\.googleapis|fonts\\.gstatic" gateway tenant-app`

Expected: no matches.

- [x] **Step 3: Refresh the codebase graph**

Run: `graphify update .`

Expected: an incremental AST update with no extraction errors.

## Deferred facts requiring owner confirmation

- Exact retention periods for account, payment, backup, and log data.
- Actual processor list and the countries hosting mail, monitoring, backups, databases, and payments.
- Whether any Wildberries-derived records identify third parties.
- Registry status and the content of any Roskomnadzor notification.
