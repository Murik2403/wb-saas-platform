# Mobile link configuration follow-up

> **For agentic workers:** Execute the bounded configuration task and verify the rendered Compose configuration before claiming completion.

**Goal:** Let real Android/iOS association identifiers reach gateway through Compose; retain empty associations until owner signing identifiers exist.

**Architecture:** Existing gateway handlers read the three optional environment variables. Add passthrough and documented empty placeholders; correct the AASA payload to Apple's supported legacy appID/paths shape without changing route paths or creating certificates.

**Tech Stack:** Docker Compose, Python/FastAPI, Capacitor documentation.

**Spec:** Claude's mobile verification handoff on `91dcab0`, supplied by the owner after runtime release `3933d2d`.

## Constraints

- Preserve the deployed refund/PWA/backup fixes, secrets, Telegram0 and existing signing configuration.
- Do not invent Android SHA256 / Apple Team ID or create new signing keys.
- Preserve empty responses; Apple's configured AASA response uses appID + paths. Empty settings do not make App Links verified.

- [ ] Add optional gateway environment passthrough and `.env.example` placeholders.
- [ ] Correct README source/build paths and document restart/signing prerequisites.
- [ ] Reproduce the mixed AASA format and return supported appID + paths; retain Android and blank defaults.
- [ ] Run existing mobile gateway/shell/download tests; verify real Compose renders configured synthetic values and empty defaults.
- [ ] Commit only scoped changes; install the optional passthrough on host, verify unchanged live associations/health and record external gates.
