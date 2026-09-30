# Restore public gateway focus and readable secondary text

**Goal:** Fix the confirmed regression in LIVE c4e15baf gateway templates without importing an older release branch.

**Architecture:** Presentation-only template change. Raise faint text to #858ba1, keep purple branding, define a focus token and a shared3px keyboard indicator. Preserve endpoints, form actions, fields, owner checks and privacy behavior.

**Spec:** marketshelper-audit.md recommendations2/3. Current read-only public GET/login200 has faint#5b6178, no focus-visible, inputoutline:none; /calculator has same faint/reset. This agrees with exact sourcec4e15baf.

## Tasks and files

- [x] Add gateway/tests/test_live_focus_contrast.py: WCAG relative-luminance contrast >=4.5 for faint on bg/surface/surface-2; actual Jinja render/login/register/calculator includes shared link/button/input/select/textarea/summary/tabstop focus3px and has no field outline suppression; marketing rule uses same focus role.
- [x] Run the focused tests against unchanged c4 and record red failures.
- [x] Change only gateway/templates/base.html and marketing_base.html: fainttoken, focusrole, commonselectors and remove fieldoutline resets. No frontend or Vite changes.
- [x] Focused and full gateway tests, diff-check, independent source review, exact scopedcommit fromc4.
- [ ] Runtime keyboard/mobile acceptance and encrypted deployment are root-owned release gates. Source tests cannot certify keyboard order, focus clipping, screen reader or complete WCAG.

Graphify query attempted first; graph.json absent, so this bounded presentation review uses exact templates and the prior audit.

## Evidence

Focused red5→green5 (actual full calculator page substituted for partial include), fullgateway392 passed/177subtests/2skipped, independentreview5 passed; root accepted minimal CSS diff. Faint contrast on bg/surface/surface-2:5.80/5.42/4.94. Static actualJinja fixture5194 has captured template hashes; no actual authentication/API or POST. Browser keyboard/mobile and production acceptance remain unverified here.
