# SEO operating cadence

## Every week

1. Review Search Console and Yandex Webmaster queries and landing pages. Record only observed queries, clicks, impressions, and position; unknown demand stays `unverified`.
2. Select one topic from `SEO_CONTENT_QUEUE.md`. Check that it has one owning URL and does not duplicate an existing guide.
3. Fact-check every product claim against the current route, UI, and API. Mark unsupported automation as manual or confirmed-data input.
4. Publish at most one reviewed article. Run route, canonical, sitemap, and internal-link checks before deployment.

## After each deploy

- Fetch every public marketing route and `/sitemap.xml`.
- Check status, canonical, title, description, H1, robots, and JSON-LD where expected.
- Record the commit, route results, and any warnings in the release note.

## Monthly

- Refresh the cluster registry from observed demand and current route ownership.
- Retire duplicate or unsupported topics.
- Compare registrations, trial activation, and paid conversion with the existing growth funnel report. Visits alone are not a success metric.

## Stop conditions

Stop publication when demand is guessed, a claim cannot be verified, two pages target the same intent, a canonical or internal link is broken, or the product CTA describes functionality that is not live.
