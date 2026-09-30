# Reduce repeated secondary-shop API cold startup

Observed exact 2055 browser GETs complete in serial roughly 6-second increments, finance 36.329 seconds. Offline fresh synthetic import profile: API import 4.828 seconds, schema startup 0.063; Streamlit helper imports and PDF/matplotlib dominate. Preserve the single cross-process child slot and all scoped request, ACL, snapshot and timeout contracts.

1. Add a fresh subprocess regression that forbids importing Streamlit/matplotlib when merely importing the API and reading formatting/report labels. Run against the old source and observe failure.
2. Move the unchanged metric-label dictionary into a small dependency-free catalog, re-export it from chart metrics, and use catalog for API/report-service metadata.
3. Import Streamlit/Plotly only inside the seven legacy rendering functions that actually need them. Pure data helpers retain behavior.
4. Keep callable PDF entry points in API/scheduler but defer importing the real builder until a PDF is requested. No chart or financial formula changes.
5. Fresh regression, complete tenant/report and API suites, actual PDF native gate and before/after offline import profile. Independent review before commit. Combine with the next audited source tranche, then exact-image/native/backup/browser/live deployment; do not claim this source patch deployed before then.
