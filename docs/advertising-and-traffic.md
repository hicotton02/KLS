# Advertising and traffic

## AdSense verification only

The production site uses publisher `pub-4907492213987533` through the server environment
variable `KLS_ADSENSE_PUBLISHER_ID`. It adds Google's account verification meta tag and
serves the authorized seller line at `/ads.txt`. No AdSense script, ad units, or Auto ads
are enabled by this change. `KLS_ADS_TXT` remains an optional explicit override.

In the owner's AdSense account, add `keepinglawsimple.org` under Sites, verify using
the meta tag or ads.txt, and request review. Keep Auto ads off. Account review and any
future ad placement need separate confirmation; a publisher ID is not approval.

Google's instructions: https://support.google.com/adsense/answer/7584263?hl=en

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

- Run backend tests, build the site, and run both Node test files.
- Confirm production `/ads.txt` and the homepage meta tag; confirm no ad script loads.
- Make a labeled automated page request and verify a new `site_server` bot row.
- Confirm prefetches and health checks do not create rows; retries do not add duplicates.
- Confirm `/internal/site-page-views` returns 404 without the shared token.
- Inspect GA4's last 30 days in the owner's account before quoting audience or revenue.
  A configured measurement ID or a successful tracking request does not prove report access.
