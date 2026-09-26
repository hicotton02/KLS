# Weekly Health Check - September 26, 2026

## Bottom Line

The public site is working. A small West Virginia feed repair is live and tested.
Its full rescan is still running, so the feed is not yet marked fully recovered.
One Wyoming video still blocks audio downloads. No emergency owner action is needed.

## Live Checks

- Confirmed context `kubernetes-admin@kubernetes`, server
  `https://192.168.104.50:6443`, and namespace `keeping-law-simple` before changes.
- Frontend 2/2, API 2/2, database 1/1. All five service pods had zero restarts.
  The API rollout kept `maxUnavailable: 0`; both new pods use the expected digest.
- All service targets were ready. The current Envoy Gateway HTTPRoute attachments
  were accepted with resolved references. Both storage claims were bound.
- PostgreSQL accepted connections, with no blocked queries or waiting locks.
  Its disk was 18% full. No namespace warning events were present.
- No failed or stalled Jobs or leftover batch pods were present at the first check.
  Finished jobs have short retention, so their old logs were not available.
- The daily feed runs at 2:15 AM Mountain time, using 32 workers for 52 areas.
  Wyoming discovery runs hourly; transcription and reasoning run every five minutes.
  All four schedules were enabled and used digest-pinned images.
- After release, 124 public checks passed: all 52 area pages, all 52 area APIs,
  homepage, health, readiness, Wyoming directory, Eric Barlow search/profile,
  2020 SF0139 page/API, and supporting routes. An invalid area returned 404.
  Barlow returned exactly one person. SF0139 still had a ready summary and sources.
- Desktop (1440px) and mobile (390px) browser checks passed without page errors or
  horizontal overflow. Search submission and the official-summary disclosure worked.
- Initial bounded logs covered up to 1,500 lines per pod from the previous day.
  API and database logs had no errors. Frontend logs had four brief analytics delivery
  failures and invalid server-action references (`x` and `r2s`). Normal browser flows
  did not reproduce them. New page views were reaching the database, and fresh
  API/frontend logs were clean after rollout. This is not a full traffic log audit.

## Feed Status

All 52 sync rows were present. At the first check none were stuck running, and all
reported a last-success date from this morning, between 2:15 and 5:41 AM Mountain.
The database held 190,733 bills. No reported official total exceeded the stored total.
These counts compare the configured scan scopes, not every historical bill.

There were two limits to that result:

- West Virginia reported one failed bill refresh: SB991. Its stored total was still
  2,777 of 2,777. A fresh last-success date does not erase a per-bill failure.
- Federal totals were unknown, not zero. The live feed checked the latest 40 bills,
  and the database held 90 bills from the 119th Congress. Full federal coverage is
  not verified. Expanding that feed is separate work, not a healthy-status shortcut.

## Repair And Release

West Virginia's daily run could not parse SB991's bill number. Live retries of the
same official URL returned the expected heading, title, and February 18 action.
The original failed response body was not retained, so its exact contents are unknown.

The detail fetch now uses the existing bounded HTTP retry helper, with three attempts
for temporary HTTP/network failures. A page missing its bill identity gets one extra
attempt after one second. Persistent bad content still fails. Access-denied and
missing-page responses are not retried, and bill identity is never guessed from a URL.

- Focused tests: 10 passed. Full backend tests: 319 passed. Frontend tests: 31 passed.
- A private canary passed 58 API checks against production data, including all areas
  and the real SB991 source. Database read-only mode was verified. The canary had no
  public service selector or ingress, and analytics were disabled.
- The image adds only `app/westvirginia_api.py` to the previous production image.
  Dependencies, frontend, database schema, and shared GPU services were unchanged.
- API deployment and all four CronJobs now use:
  `registry.skazproconsulting.com/keeping-law-simple/web:20260926-weekly1@sha256:5184ec268df8549a597f4fc100fb3462afa6615b2d885b20a1812bc30a8a4eb2`.
- A full, source-only West Virginia recovery job started at 6:09 AM Mountain:
  `kls-weekly-20260926-wv-recovery`. It has a six-hour deadline and no automatic job
  retry. Its result must be checked before calling the old failure cleared.
  At 6:11 AM it had checked 50 bills with zero new failures, confirming progress.
  The normal sync resets current-run counters; zero failures during this run does
  not prove it has finished. No failed counter was manually cleared.
- The temporary build Job and canary were removed after verification.

## Wyoming Sources

All 2,644 usable transcripts have completed explanation scans. There are 14,352
published or curated reasons. No transcription or explanation worker was stuck.

Recording 480, the March 8, 2024 Senate PM2 session, remains retryable. Live metadata
marks it public, with no caption tracks, but a bounded audio request returned HTTP
403. This is an access failure, not proof of a deleted or private video. Existing
cooldown retries remain in place; no login, network bypass, or quality rule was changed.

Source gaps are separate from active work: 17 unavailable/private videos, five invalid
links, 15 rejected transcripts, and 99 duplicate entries. Those were not requeued.
Bill scan records include 619 partial scans, 25 source-unavailable records, and two
needing review. A completed worker queue does not mean every bill has a published reason.

## Speed And Owner Follow-Up

After rollout, the sitemap took 3.0 seconds, homepage 2.53 seconds, overview API
2.41 seconds, and New York page/API about 1.70 seconds. These were the main response
time outliers. The cold browser homepage check took about eight seconds including
assets, browser idle time, and the screenshot; that is not the HTML response time.

No urgent owner action is needed. A permitted copy or alternate official source
would help recover the blocked Wyoming recording. Federal backfill needs separate
scope and capacity planning. Ads remain off. No AdSense account settings, credentials,
private Sites content, privacy settings, or broad network configuration were changed.
