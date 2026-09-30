# Accessible authentication form errors

Actual rendered login error is plain text outside the form, without an alert role or a programmatic association. Apply only presentational semantics to login, registration, password-request and password-reset templates.

1. Render regressions with and without an error; require a unique alert and a form reference only when the error exists.
2. Add role=alert and a stable form-error ID; describe the form from that node conditionally. Preserve escaping, actions, validation and credential handling. Do not mark individual credentials invalid for a form-wide error.
3. Run targeted and full Gateway suites, independent review, actual rendered browser/error/focus acceptance, then exact-image encrypted restore and live deployment. Screen-reader speech is not claimed from DOM semantics alone.
