# Selected WB shop / Ozon boundary followup

Root-approved contract: additional shops are verified WB identities only. No Ozon seller identity or credential relationship is established. Every Ozon-accounting route is therefore forbidden for secondary WB shops. Primary legacy behavior remains available and its resource scope is normalized to tenant_shared.

1. Refresh the frozen frontend registry fixture from the reviewed backend Ozon scope correction; preserve unrelated route metadata.
2. Add regressions over all actual Ozon methods: secondary calls reject before fetch, including reads, sync, cost, manual movements, templates and lots. Primary calls accept server tenant_shared response headers with the ordinary legacy request semantics.
3. Run focused transport tests and the production frontend build. Request independent source review and commit only this followup. No application monetary changes or deployment.

Native Nginx acceptance keeps its primary Ozon tenant_shared assertion. It must be repeated against the newly built immutable image after backend scope correction; cc8b430 partial runtime evidence does not meet this corrected boundary.
