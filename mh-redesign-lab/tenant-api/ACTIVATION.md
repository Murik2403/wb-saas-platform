# First useful report: stage 1 contract

This slice uses the existing single-product economics calculation. It does not
claim readiness is activation, send emails or backfill past activation.

## Readiness

`GET /api/v1/growth/activation?nm_id=123` reads local tenant evidence. It requires:

- a non-demo tenant and a saved WB connection;
- the latest full import to have `success`, a finish time and the `orders`,
  `sales`, `financial_rows` result markers (ads/finance-only refreshes do not count);
- a selected SKU with a finite, positive saved unit cost and an update time;
- actual quantity or revenue in financial rows for that SKU.

Zero cost, a catalogue row, a failed/partial/running import, another SKU's data,
and demo data never satisfy this definition. Existing first-value evidence is
reported separately. GET does not create evidence. It may retry a previously
recorded pending outbox event through the configured internal transport.

## First report and acknowledgement

`GET /api/v1/growth/first-report?nm_id=123` calculates the existing Products
economics for the selected SKU, over the 30 days ending on its last available
financial date. It uses `build_dashboard` and `compute_products`; finite cost,
sales/revenue and accounting-backed margin must exist. No default zero profit
is substituted for an unavailable calculation. The explicit period and limits
are shown in the report: tax and external expenses missing from the accounting
inputs are not covered.

After the report card renders, React sends:

```json
{"nm_id":123,"report_revision":"sha256-of-the-returned-report"}
```

to `POST /api/v1/growth/report-viewed`. The tenant API requires authenticated
`X-Tenant-Account` and owner/manager `X-Tenant-Role` headers from the existing
gateway/Traefik boundary, checks readiness and recalculates the report. A changed
revision returns 409 and requires reopening the report. These forwarded headers
are not an independent authentication system; tenant ingress must remain behind
the gateway which sets them.

The marker in `growth_first_value` and `growth_event_outbox` are written in one
SQLite transaction. Repeated acknowledgements retain the original UTC time.
Only event name, stable key and occurrence time are delivered to the gateway;
SKU, report figures, WB data and tokens stay within the tenant. Missing internal
transport configuration leaves the outbox pending. Demo acknowledgements fail.

This is evidence that an authenticated client rendered the result, not proof
that a person read or accepted every figure. The positive saved total unit cost
is the explicit v1 cost boundary; the service cannot verify the owner's source
documents or infer omitted external costs. Gateway cohort rules must exclude
existing customers from new-trial A7; this code never invents a historical date.

## Checks in this isolated copy

- Python readiness and HTTP tests cover partial/failed/running/ads-only imports,
  missing/zero/nonfinite/other-SKU costs, absent financial data, demo blocking,
  identity/role checks, incomplete computed economics, revision mismatch,
  idempotency and atomic rollback of marker/outbox.
- React tests cover the same visible failed-import/zero-cost states and verify
  that no acknowledgement occurs until the requested report has loaded and its
  card has rendered; failed report loading creates no acknowledgement.
- Production data, actual WB requests and production deployment were not used.
