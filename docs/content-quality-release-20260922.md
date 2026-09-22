# Content Repair Release

Released September 21, 2026, Mountain time (September 22 UTC).

## Result

The production site has the new content checks. Ads remain disabled. No AdSense
review was submitted, and no private Sites project or privacy setting was changed.

This is a technical cleanup, not a claim that Google has approved the site or
that a human reviewed every summary. The next editorial steps are in
[the review checklist](adsense-review-checklist.md).

## What Changed

- The homepage features explanations with source links and substantive details.
  Empty, title-only, fallback, and stale explanations no longer qualify.
- Reference-only bill pages use `noindex, follow` and stay out of bill sitemaps.
  Their official links and history remain available. Useful Wyoming vote archives
  remain indexable without being ad-eligible.
- The API, bill pages, homepage, ad placements, and sitemaps use the same content
  checks. These checks concern the completeness of the page, not the merits of a bill.
- Missing source links no longer receive a source badge. Unfinished explanations
  do not display empty "Who it affects" and "Limits" sections.
- The editorial standards page explains automated drafting and its limits without
  inventing a human reviewer.
- Minnesota's parser now separates introduced/engrossed versions from enrollment,
  reads chamber headings, keeps multiple actions in a cell, accepts only real dates,
  and does not let an undated referral replace a later dated action.
- A source-version-guarded Wyoming budget correction is stored and survives refreshes.

## Data Verification

All 3,639 Minnesota records were re-read from their official pages. One Wyoming
budget record was corrected. Backups cover all 3,640 distinct records. The final
pass checked 3,640, applied 3,207 changes, skipped already-correct records, and had
zero failures or concurrent-write conflicts. The earlier pass was stopped when an
undated-action ordering problem was found; its backup was retained and the corrected
pass rechecked the entire scope.

Readback found zero Minnesota "Final Actions" date values and zero introduction or
engrossment labels stored as enrollment. The total bill count stayed at 190,382.
No bill, vote, or source record was deleted.

The example [Minnesota SF3794](https://www.revisor.mn.gov/bills/94/2026/0/SF/3794/)
now follows its dated action instead of the undated referral listed at the bottom
of the source display. The Wyoming correction links to the
[official SF0001 summary](https://www.wyoleg.gov/2026/Summaries/SF0001.pdf).

New Hampshire's earlier source-list HTTP 500 had recovered. A normal metadata-only
refresh completed for all 1,387 bills with zero failures. No status totals were
manually cleared. Final `sync_status` readback: 52 jurisdictions, zero running
rows, zero failed totals, zero source/stored count deficits, and an oldest success
on September 21, 2026.

## Release Checks

- 314 backend tests passed, including repair idempotency and concurrent-update protection.
- 31 frontend/consent/analytics tests passed, including rendered thin/reference/archive cases.
- The Linux frontend build passed.
- Isolated canaries used production data with database read-only mode verified on.
  They had separate selectors and internal-only services, with no ingress or public traffic.
- Desktop (1440px) and phone (390px) browser checks passed on the homepage, corrected
  bill, unfinished record, and historical voting record. No overflow, missing assets,
  JavaScript errors, or ad SDK/placements were observed. Source expanders worked.
- 120 public routes passed, including all 52 area APIs and pages, Wyoming's 2020
  SF0139 page/API, and the legislator search returning one Eric Barlow record.
- Deployment updates used digest pins and `maxUnavailable: 0`. Both services have
  two ready replicas; new application pods had no restarts.

Observed response-time outliers: Hawaii page about 3.8 seconds, sitemap index about
3.0 seconds, overview API about 2.9 seconds, homepage about 2.7 seconds. These are
successful responses but remain performance follow-ups.

The first site build hit a temporary 3Gi memory cap. A new build with an 8Gi cap
completed; production resource limits were not raised. A PostgreSQL placeholder
issue was caught in the read-only canary and fixed before that image reached production.

## Image Pins

- API and all four CronJobs:
  `registry.skazproconsulting.com/keeping-law-simple/web:20260922-content4@sha256:12f9086c1d0a58e9139a411a8a1a87a188c98f3c2fa26cc28b32d03467853cd9`
- Frontend:
  `registry.skazproconsulting.com/keeping-law-simple/site:20260922-content2@sha256:27758276504acd96f4353f812eb43787087592b4256296d13ad562defbe77f10`

API builds layered the changed application onto the prior immutable dependency image.
The frontend was built in Linux from its existing lockfile. No schema change was needed.

## Remaining Work

- A real publisher/editor identity and human review of useful Wyoming guides are
  still needed before another AdSense request. The new guide is a repository-only
  draft, not a website article or a human-reviewed article.
- Automated validation is not a full factual audit of all 190,382 records. Other
  source adapters and summaries still need sampling and editorial review.
- Existing lint fails on the plain home-page link in `AdPrivacyChoices.tsx`.
  Consent-navigation behavior was left unchanged in this content repair.
- Existing dependency audit findings were not broadly upgraded: 21 total, including
  one moderate production-tree finding for `baseline-browser-mapping`
  ([advisory](https://github.com/advisories/GHSA-w5vr-8v7q-w6rv)). No high or critical
  production-tree finding was reported by `npm audit --omit=dev`.

Backups are outside Git, on the control plane under `/tmp/kls-content-20260922/`
and in the repair worktree's ignored `tmp/` folder. Keep both copies until the
owner accepts the repaired records.
