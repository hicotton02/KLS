# Advertising and traffic

## Manual AdSense display unit

The production site uses publisher `pub-4907492213987533` through the server environment
variable `KLS_ADSENSE_PUBLISHER_ID`. It adds Google's account verification meta tag and
serves the authorized seller line at `/ads.txt`. `KLS_ADS_TXT` remains an optional explicit override.

The owner confirmed site approval, a published three-choice Google consent message,
and Auto ads off. Before enabling ads, also confirm Politics is blocked in AdSense's
Brand safety > Content > Blocking controls > Sensitive categories. The public
advertising policy excludes political ads; Google category blocking is best effort,
so review served ads and block any that slip through.

`KLS_ADSENSE_DISPLAY_SLOT=8149837735` selects KLS Display. Only the exact runtime flag
`KLS_ADS_ENABLED=true` enables placements. False or missing is verification-only and
is the rollback switch. The checked-in manifest keeps ads disabled pending account
checks and end-to-end verification.

There is at most one compact, below-content ad on the homepage, nonempty bill lists,
and bills with a validated summary. No ad slots appear on search, empty results,
legislator profiles, vote explanations, policy/contact pages, or errors. The Google
SDK loads once and requests wait for its certified CMP, then for a near-viewport
placement. Unknown consent, declines, script failure, browser GPC, and the local
privacy-page opt-out keep ad requests off. US privacy changes pause requests until
a fresh page load checks the new decision. Google validates the complete TC string.

All units request non-personalized ads and restricted data processing. Analytics
consent is separate and never grants advertising consent. Empty units collapse;
ad requests are not refreshed on resize. Keep Auto ads off in the account.

Google's instructions: https://support.google.com/adsense/answer/7584263?hl=en
Consent API: https://developers.google.com/funding-choices/fc-api-docs
Ad pausing: https://support.google.com/adsense/answer/7670312?hl=en
Ad sizes: https://support.google.com/adsense/answer/9183363?hl=en
Restricted processing: https://support.google.com/adsense/answer/9598414?hl=en
Political categories: https://support.google.com/adsense/answer/164131?hl=en

### September 13 readiness check

- Candidate image: `registry.skazproconsulting.com/keeping-law-simple/site:20260913-adunit2@sha256:ddba29f63395d27492dd91366425e246715fd1621dcd4763c33308cd5eb84eb3`.
- Local build, Linux image build, and all 30 Node tests passed. Private Linux-pod browser
  checks passed at 320, 390, 768, and 1365 pixels, including consent changes, local opt-out,
  privacy-page script exclusion, client navigation, duplicate prevention, and unfilled ads.
  Google ad traffic was mocked or blocked; no paid impressions or ad clicks were generated.
- The whole-project TypeScript check still reports existing missing Cloudflare worker
  types in `db/index.ts` and `worker/index.ts`; none of the new ad files produced errors.
- A real SDK probe on the public site, including Google's GDPR preview parameters,
  did not receive Google CMP callbacks or display its consent message. The same happened
  with the standard Google snippet. The cause is not confirmed. Verify the published
  message's site assignment and delivery before enabling ads; do not bypass the gate.
- Politics blocking is still awaiting owner confirmation. Production remains on its
  previous image with ads off. Deploy the candidate and change the runtime flag only
  after both account checks and the real consent test pass.

## Current site page logging

The standalone Node server imports `server-analytics.mjs`. Completed public HTML and
RSC page responses enter a bounded, asynchronous queue. Prefetches, probes, assets,
private pages, and errors are excluded. A server-only shared token authorizes batches
at the backend's `/internal/site-page-views` endpoint. Do not add a public ingress route
for this endpoint or expose the token through a NEXT_PUBLIC variable.

Create the Kubernetes secret `keeping-law-simple-site-analytics` with a strong random
`KLS_SITE_ANALYTICS_TOKEN` key before applying the web and site deployment manifests.
Only these two deployments receive it. Deploy the backend before enabling the sender.

The sender holds at most 256 events per pod, sends batches of 16, times out after five
seconds, and retries failures up to three times with the same event IDs. Overflow,
permanent failures, and a pod restart can lose events; this is best-effort analytics,
not a billing ledger. Delivery failures are logged without tokens or visitor data.
The receiver rejects oversized, stale, or malformed batches. Database event IDs make
retries idempotent. A nullable event ID preserves legacy records.

Queries, cookies, and full referring URLs are not sent. Client IPs travel only over
the internal service connection, are used for the existing location lookup and masked
visitor signature, and are not stored in the page-view table. Bot filtering is an
estimate, not proof that every remaining request came from a person.

New rows have `tracking_source = 'site_server'`. The admin analytics page separates
these from the old logger and shows the latest receipt. Old totals are not reliable
enough for advertising decisions. Cached client navigations may not hit the server;
use consented GA4 reports for users, sessions, traffic sources, and client page views.

## Verification

- Build the site, then run the rendered HTML, server analytics, and ad-consent Node tests.
  On Windows, set `WRANGLER_LOG_PATH` in PowerShell and run the vinext CLI directly;
  the existing npm build command uses Unix environment assignment.
- Run `node tests/display-ads.browser.mjs` against an ads-enabled local/canary server.
  Set `KLS_BROWSER_TEST_URL` and optionally `PLAYWRIGHT_MODULE` to an installed module.
  Use `KLS_BROWSER_TEST_OUTPUT` for screenshots. Google responses are mocked: never
  click live ads or generate paid impressions as a test.
- Confirm `/ads.txt` and the homepage meta tag. With the switch off, no ad script loads.
  With it on, verify real Google CMP callbacks before releasing public ad requests.
- Make a labeled automated page request and verify a new `site_server` bot row.
- Confirm prefetches and health checks do not create rows; retries do not add duplicates.
- Confirm `/internal/site-page-views` returns 404 without the shared token.
- Inspect GA4's last 30 days in the owner's account before quoting audience or revenue.
  A configured measurement ID or a successful tracking request does not prove report access.
