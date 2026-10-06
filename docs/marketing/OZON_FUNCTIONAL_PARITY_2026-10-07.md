# Ozon: implementation and acceptance boundaries

## Implemented

The main cabinet's Finance → Stores and scenarios → Ozon workspace now has five lazy tabs. Additional WB shops cannot fall back to the main Ozon seller. Ozon documents, plans and favourites use an explicit namespace and never alter WB stock.

- Source/document reconciliation: signed RUB fee components are reconciled to source totals. A product accrual can be explicitly linked to existing documentary events with matching SKU, accrual date and total. Historical FIFO comes from those documents and lots, not an inferred physical movement. Confirmation checks the current source/documents/lots fingerprint and records an immutable audit link; reload/correction does not append the old source total again.
- Product economics, heatmap and local plan/fact/favourites. Orders are created postings, including cancelled postings; they are not sales. Sales, returns, revenue and FIFO come from documentary movements. Product ID never substitutes for SKU. Stale overlapping API snapshots are hidden; independent documentary reports remain usable. Unknown daily values/forecasts remain unknown.
- Monthly documentary profit and loss, CSV, and explicit tax inputs for the six existing calculator regimes. Ozon accruals and document revenue never automatically fill received taxable income. Empty tax fields are missing data, while a confirmed zero must be entered. The estimate does not mutate WB settings or silently subtract a tax twice.
- Advertising: dedicated encrypted Performance API credentials, explicit campaign selection, asynchronous report creation and bounded single-step polling, saved complete campaign snapshot, campaign/day metrics and CSV. Direct orders/revenue remain distinct from model attribution. Missing dates or metrics are not fabricated. The legacy ad agent now refuses unsupported budget decisions instead of returning fake zero statistics or using Seller credentials as Performance credentials.

## Contract evidence

Primary sources reviewed: [Ozon Seller API](https://docs.ozon.ru/api/seller/) (`GetFinanceAccrualByDay`, `GetFinanceAccrualPostings`, `GetFinanceAccrualTypes`) and [Ozon Performance API](https://docs.ozon.ru/api/performance/) (`SubmitRequest`, `StatisticsCheck`, `DownloadStatistics`, `GetCampaignDailyStats`), plus the official downloadable CSV example. Seller finance fields expose money, category/type and separate posting evidence; their descriptions do not establish a complete physical sale/return identity and historical movement contract. A source quantity or unit number alone is not converted into a FIFO sale.

## Resource and correctness checks

SQL row/byte gates run before JSON decoding for source snapshots, documentary histories, FIFO projections and reconciliation links. Namespace filtering precedes decoding in the new reconciliation panel. Heavy local Ozon reads use the existing complete-request memory guard; upstream synchronization and Performance polling do not block local WB finance behind external rate-limit backoff.

Independent review found and fixed: daily CSV totals parsed as dates; monetary aggregation losing kopecks through float; unbounded histories in reconciliation/legacy P&L. Monetary totals and exports retain Decimal strings. Known fields in CSV detail rows are reconciled against declared totals.

## Still requires real evidence

This release is **not full automatic Ozon/WB feature parity**. Nonempty real Ozon operations and returns have not been supplied for financial acceptance. Marketplace source completeness remains unconfirmed even where a saved collection is fully paginated. Unlinked sales/returns remain blocked rather than guessing quantities, dates or return origins. RNP forecasts do not replace unavailable sales history. Automatic Ozon ad budget optimisation is not enabled; the new report panel is read/report functionality.

Performance analytics requires separate Performance API credentials and real selected campaign reports. Current Seller credentials are preserved. Tests, synthetic UI examples and healthy empty live endpoints do not prove real financial reconciliation. Source schemas and actual examples must be validated before widening automatic posting or changing the verified flags.

## Release evidence

- Feature commit: `fd25c6aeac49e71300e2e83f45db2ef700bacbb0`; session-mode follow-up and deployed runtime: `d4446faa326fb95929e8e65edb6d1cc108db058f`.
- Both tenants: `mh-tenant:ozon-d4446fa`, image `sha256:411ea80117b5dd8fe3d7eef58f355dc185b7ddd3cb82a06289542e5faf673f55`, memory limit1536MiB. Existing runtime environment, keys, volumes and network aliases preserved. Candidate and rollback rehearsals restored encrypted backups independently.
- Frontend compiled from clean committed fd25c6a source. All eight frontend input Git trees equal d4446fa; output byte hashes verified. Source proof includes this equality and the immutable base-image chain.
- Fresh validation:978 tenant-app tests +10 subtests passed,1 skipped;578 API tests;508 frontend tests; TypeScript and clean build passed. Dependency lock unchanged; npm clean installation audited149 packages with0 vulnerabilities.
- Live HTTPS health for demo/mvvorokov is200/ok. Seven new read routes per tenant return200;0.011–0.069s on currently empty source/document data. This is an empty-state operational check, not a nonempty financial acceptance.
- Cold September custom finance inside mvvorokov:200 in6.24s. Six concurrent existing dashboard GETs:all200, no OOM/max events or restarts; sampled peak767299584bytes (731.8MiB). Serialized queue latency4.829–18.553s; this stress result does not promise every concurrent request is below10s.
- Production demo session now reports `mode=demo`, matching server write denial, and hides Ozon credential/import/edit controls. Real cabinet reports `mode=tenant`.
- Provisioner runtime unchanged; future tenant pin set to `mh-tenant:ozon-d4446fa`; `provisioner` network alias and gateway health connection checked. Traefik untouched.
- All16 narrated course videos and16 caption tracks verified with hashes; range playback and public security headers retained.
- Browser acceptance: local nonempty synthetic product/FIFO/monthly example, heatmap/plan-fact presentation, both themes and320px no body overflow. Production demo's new tabs and safe empty advertising state checked. The local direct API fixture does not inject gateway role headers: its browser plan save correctly returned403; owner/manager saves are covered by API tests, not represented as a passed production write.

Operator artifacts: `C:/Users/mvvor/Downloads/wbsaasplatform/deliveries/ozon-parity-20261007/release2/` and `/opt/wb-saas/releases/ozon-d4446fa/release/`. Backup/rollback proof and source fingerprints are retained there; secrets and private financial values are not printed. Foreign TrueStats review remains unstaged and unchanged.
