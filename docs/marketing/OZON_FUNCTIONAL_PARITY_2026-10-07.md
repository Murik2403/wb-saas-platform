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

Release revision, clean-source build, encrypted backup restore rehearsals, live health/route timings, memory checks and frontend acceptance are recorded after deployment. Foreign TrueStats review remains unstaged and unchanged.
