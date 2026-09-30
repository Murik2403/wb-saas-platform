# Bounded drawer keyboard focus and accessible dialog names

**Goal:** Make the existing App drawers reachable and escapable by keyboard without changing their operations or CSS.

**Spec:** Design audit calls for actual keyboard order/focus and screenreader text checks. Source ef616ed has13 aria-modal section/aside drawers without initial focus, confinement, Escape or opener restoration. Native interaction dialog has a heading but no accessible-name linkage.

**Architecture:** A small shared focus scope attached by refs to the existing App dialog roots; closures continue to call existing onClose/onCancel. Maintain a stack for nested mounted drawers and avoid intercepting a different native showModal dialog. Focus first enabled visible control or the dialog root; trap boundary Tab/ShiftTab; Escape closes only the top scope; restore a connected opener on cleanup. Native interaction dialog gets unique heading/message IDs and explicit focus restoration, keeping native showModal behavior.

**Files:** Create dialog-focus.ts and dialog-focus.test.tsx; modify App.tsx and its integration tests. No backend, API, money, CSS rewrite or deployment.

- [x] Test entry, Tab/ShiftTab boundary, disabled/hidden controls, no-control fallback, Escape, opener restoration and nested modal order using real DOM keyboard events.
- [x] Add App integration coverage for a real read-only drawer and native interaction accessible name.
- [x] Implement reusable hook and attach only to existing dialog components (including already-open FBS dialog).
- [x] Focused/full React gates, build, independent source review and exact commit.
- [ ] Actual matching browser keyboard/mobile acceptance remains root-owned; no actual screenreader certification is implied by DOM naming tests.

No graph in this isolatedworktree; prior Graphify query returned graph-not-found and direct dialog/ref/keyhandling inspection establishes scope.

Verification: initial App RED had two real failures (drawer focus retained on opener and native dialog missing accessible name); final focused35, full211/36 files and TypeScript/Vite build passed. Reviewer independently repeated35 tests and accepted scoped source. Capture opener before React autoFocus; seven existing pending-write cancellation guards remain enforced for Escape. Actual browser keyboard/mobile and screenreader acceptance remain separate.
