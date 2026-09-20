# SEO operating cadence for MARKETSHELPER

## Weekly

- Review Search Console and Yandex Webmaster queries, clicks, impressions, and landing pages.
- Update `SEO_CLUSTER_REGISTRY.md` only with observed evidence; mark unknown demand as unverified.
- Choose at most one article for manual fact-checking and publication.
- Check that each new topic has one owning URL and does not duplicate an existing guide.

## After every content or gateway deploy

- Check HTTP 200, canonical, title, description, one H1, visible FAQ and FAQ JSON-LD.
- Check robots, sitemap membership, internal links, and UTM canonical cleanup.
- Run the public SEO audit and retain the JSON result with the release evidence.

## Monthly

- Review pages with meaningful impressions and positions roughly 4-15.
- Improve the page with new verified examples, source links, and answers to observed questions.
- Do not change a publication date without substantive content changes.

## Business outcome

- Use `scripts/report_growth_funnel.py` for registrations, trials, first useful reports, and payments.
- Treat search visits, clicks, and rankings as leading indicators; do not report them as revenue.
- Keep test accounts, unknown attribution, returned payments, and consent-denied sessions separate.

## Stop conditions

- Stop publication if a claim cannot be supported by current product behavior or a primary source.
- Stop a topic if it conflicts with an existing owning URL or creates a near-duplicate.
- Stop a release if canonical, sitemap, structured data, or core CTA checks fail.
- Stop paid promotion separately when the funnel or event attribution is broken.
