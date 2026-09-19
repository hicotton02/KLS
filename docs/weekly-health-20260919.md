# Weekly Health Check - September 19, 2026

## Verified Baseline

- Confirmed `kubernetes-admin@kubernetes`, API `https://192.168.104.50:6443`,
  namespace `keeping-law-simple` before changes.
- Frontend 2/2, API 2/2, PostgreSQL 1/1. The two API pods each had one restart
  on September 17, with no new restart during the check. No namespace warning events.
- Services and EndpointSlices had all expected ready targets. The Envoy Gateway
  HTTPRoutes were accepted and references resolved. PVCs were bound; PostgreSQL
  used 17% of its disk, accepted connections, and had no blocked queries or locks.
- All 52 public area pages and 52 valid area APIs returned 200. Homepage, health,
  readiness, Wyoming directory, Eric Barlow search/profile, and 2020 SF0139 page/API
  passed. Barlow returned exactly one person. SF0139 retained a validated summary.
- The correct historical page is `/area/wyoming/bill/2020/SF0139`.
  Initial probes used two nonexistent paths; their 404s were probe mistakes,
  not broken links found on the site.
- Desktop (1365px) and mobile (390px) browser checks passed without page errors or
  horizontal overflow. New York was the slowest initial area page at 1.77 seconds;
  its area API took 1.63 seconds. Most routes took less than one second.
- All 52 sync rows existed, none were stuck running, and the oldest successful
  scan was September 18. Known source totals did not exceed stored totals.
  Failed list fetches can leave totals unknown; a missing total is not proof of coverage.
- The daily source job runs at 02:15 America/Denver with 52 indexed completions
  and parallelism 32. Wyoming discovery runs hourly; transcription and reasoning
  run every five minutes. All four CronJobs were enabled and digest-pinned.
- No leftover batch pods or Jobs existed at the initial snapshot. Expired jobs
  and their logs were not available to review. The daily job's last successful
  completion was September 18; September 19 source errors were visible in the database.
- Bounded API/database logs had no errors. Frontend logs contained aborted or
  invalid server-action requests; normal desktop/mobile navigation did not reproduce them.

## Confirmed Issues And Repairs

The initial source status contained 2,845 refresh failures across six jurisdictions:
Colorado 626, Wisconsin 2,213, North Carolina 3, Hawaii 1, Michigan 1, Massachusetts 1.

- Colorado returned HTTP 406 when its bill detail page received a generic Accept
  header. Explicitly requesting HTML returned 200 with the expected bill fields.
  The connector now sends that header for detail pages, without changing identity,
  credentials, TLS verification, or source URL.
- Wisconsin used direct GETs for its index, bill details, and proposal text.
  These now use the existing bounded retry helper for timeouts, 429, and temporary
  server errors. Tests cover all three stages and confirm that access-denied
  responses are not retried. A source that remains unavailable still fails visibly.
- Two Wyoming recordings (1290 and 1205) repeatedly produced malformed output.
  Live reproduction confirmed repeated text reaching the 10,000-token output cap.
  Extraction now requests a bounded JSON schema, limits names to the supplied
  roster, and rejects output-limit termination. Existing quote matching and
  vote checks remain unchanged. No incomplete response is published.
- Full backend test suite: 285 passed. Focused suite: 48 passed.

## Release Verification

Released image:
`registry.skazproconsulting.com/keeping-law-simple/web:20260919-weekly1@sha256:554565aa75349955327ad4934367ed99d1350469043a6d50f459633e61a6a093`

The image adds only `colorado_api.py`, `wisconsin_api.py`, and `ollama.py` to the
previous production digest. Runtime dependencies and the frontend are unchanged.
A private pod with a unique, non-service label uses production data for verification.
All 52 private area API checks passed with nonempty results, as did the historical
bill, Barlow search, and real Colorado/Wisconsin detail fetches.

Both failed Wyoming recordings passed full read-only extraction in the canary:
43 evidence-checked reasons for recording 1290 and 22 for recording 1205.

The API deployment and all four CronJobs now reference this digest. The rollout
kept `maxUnavailable: 0`; both new API replicas are ready with zero restarts and
the expected image IDs. All service endpoints are ready. The frontend image was
not changed. After rollout, all 114 public route checks passed, including every
area page/API. New York was slowest at 1.74 seconds. Fresh API/frontend logs were clean.

Controlled full source rescans were launched for Colorado, Wisconsin, Hawaii,
and North Carolina. Two separate targeted Wyoming recovery jobs use compare-and-set
claims and the existing extraction/evidence checks, adding results without deleting
existing reasons.

## Recovery Readback

- Colorado completed all 626 bills with zero failures and no missing records at
  06:22 America/Denver. The new success timestamp was verified in `sync_status`.
- Both Wyoming recovery jobs completed through the real background inference
  route. They added 41 and 25 reasons respectively, for 66 new reasons and 14,352
  published/curated reasons overall. Canary and production extraction counts are
  reported separately; the read-only canary did not publish anything.
- All 2,644 usable transcripts now have completed explanation scans; no explanation
  scans remain failed. The recovery jobs refreshed all 286 bill scan records for 2018.
- Newly added records for SF0121 and HB0001 were matched against the public API by
  member, roll call, full reason, and source timestamp. Both expose official recording
  links with timestamp fragments. Existing transcripts and reasons were preserved.
- Hawaii and North Carolina full rescans remain in progress, with recent database
  checkpoints and no new failures so far. They are not yet reported as completed.
  These named, time-limited recovery jobs continue without public traffic and clean
  themselves up after completion.
- Wisconsin's full rescan exhausted its bounded retries on the official index's
  HTTP 503 response. Michigan and Massachusetts remain source-side timeout/503
  blockers. Their failed status is preserved; the daily sync remains scheduled.
- The private canary and build Job were removed after verification. Completed
  recovery Jobs and the failed Wisconsin retry were removed after their results
  were recorded. Only the two running source recovery Jobs are retained.

No owner action is required for the deployed fixes. The three unavailable source
feeds need to recover before their next full refresh can pass. This check does not
claim that every upstream source is currently healthy.

## Source Gaps

At the initial snapshot, Wyoming had 2,644 usable transcripts and 14,286 published
or curated reasons. There were 17 unavailable videos, five invalid source entries,
15 transcripts held by quality checks, and 99 duplicate entries. Those are separate
from the active work queue. One YouTube recording (480) still had a download 403;
this alone does not establish that the video has been deleted or made private.

Michigan and Massachusetts timed out during bounded follow-up checks. Wisconsin's
bill detail worked, but its index returned 503 through the recovery job's retries.
These failures must not be cleared by hand or described as successful refreshes.

Ads remain off. No AdSense settings, ChatGPT Sites content, privacy settings,
credentials, GPU allocation, or shared network configuration were changed.

Structured output reference: https://docs.ollama.com/capabilities/structured-outputs
