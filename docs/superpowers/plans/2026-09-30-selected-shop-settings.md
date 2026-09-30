# Selected secondary shop settings

Actual browser defect: SettingsViewV2 Promise.all mixes primary/global credential/settings/backup reads with permitted per-shop cost reads. A forbidden global read rejects everything; secondary cost editor remains hidden despite its correct API returning30/10.

Render a dedicated secondary-only cost and cost-history panel before mounting the primary settings component. Fetch only allowed scoped product-costs/history; use selected-shop can_write and allowed_actions for edit controls, existing scoped write helper for UUID/revision/replay. Require explicit reason, positive SKU identity and finite nonnegative input; zero is an unknown cost estimate, not confirmed free inventory. No token/global settings/migration requests under secondary, no weakened backend roles. Explain that central configuration/import belongs to primary and expose trusted navigation.

Regressions: secondary amount30/10 from API, no global requests/components; read-only save disabled; exact one update with reason, failed update preserves entered values for explicit retry, history scoped tocost; primary retains existing settings path. Focused/full gates and independent review before commit. Active ROOT fixture/source unchanged.
