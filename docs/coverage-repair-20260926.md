# Coverage Repair - September 26, 2026

## Status

The public site is working. The federal catch-up fix is live. Wyoming has two
fewer bad links and one recovered recording. The remaining recovery work is not
yet complete. This is not a full source-data all-clear.

The earlier West Virginia recovery finished: 2,777 bills checked, zero failures.

## Federal Feed

The old feed checked only the latest 40 bills. It had 90 stored bills from the
119th Congress and no official total. A recent scan date did not prove coverage.

The new feed:

- Reads all official inventory pages and verifies the count and each bill ID.
- Rejects missing pages, repeated IDs, mismatched bill details, and a changing
  official count. An incomplete inventory does not replace the last good one.
- Saves pending work in PostgreSQL. Each bill is acknowledged only after saving.
- Detects text-only source changes, not just a changed action or title.
- Uses one shared lease so the daily feed and catch-up worker cannot overlap.
- Paces source requests and uses bounded network/HTTP retries. Failed bills have
  a six-hour cooldown and do not block new work.
- Reports actual stored coverage. A partial batch does not advance the complete
  scan date or hide failed bills.

`keeping-law-simple-federal-catchup` runs every five minutes, one worker at a
time, up to 100 records per batch. It has a one-hour deadline and no Job retry.
Its checkpoints survive restarts. The existing daily feed remains enabled.
Official inventory is refreshed at least when its six-hour cache expires.

At 5:35 PM Mountain, all 19,255 official IDs were in the durable queue, 48 had
completed a source refresh, and 136 matching bills were stored, with zero source
failures. Some refreshed bills were already stored. The remaining 19,207 refreshes
include both missing bills and older stored bills that need a fresh source check.
This was an in-progress snapshot, not the finished backfill count.

The next scheduled worker saw the active lease and exited without duplicating
work. New source-only pages still require the existing content-quality checks
before being indexed or showing ads. Importing a source is not the same as
finishing its plain-English explanation.

The API's pagination and request budget are documented by the
[Library of Congress](https://github.com/LibraryOfCongress/api.congress.gov).

## Wyoming Repairs

The official archive listed two bad 2018 House URLs alongside working links for
the same date, chamber, and recording order. Both replacements returned real MP3
audio. Their working records already had complete transcripts and explanations.

- Recording 1162 now points to working record 1164 as a duplicate.
- Recording 1188 now points to working record 1192 as a duplicate.
- Discovery normalizes both known aliases, preventing their return as new errors.
- Recording 1116, February 13, 2018 Senate AM, passed a full re-transcription with
  the existing quality rules. Its 201 segments were saved and its explanation scan
  finished. Published reasons and other existing transcripts were preserved.

Backups were taken before changes. The alias repair's second dry run was empty.
The saved rows and audit results are under the checkout's ignored `tmp` directory.

Recording 933 still failed a full quality check and remains held. A single bounded
recovery Job, `kls-coverage-20260926-quality-r2`, is checking the other 13 held
recordings. It uses two speech requests at a time, guarded row updates, no automatic
Job retry, and a three-hour deadline. It does not weaken quality checks. Its input
rows were backed up on the workstation before it started.

At the snapshot above, 2,645 usable recordings had completed explanation scans.
There were still 14 quality-held recordings, three invalid official links, 17
unavailable/private sources, and the public video 480 whose audio returned 403.
The latter is an access failure, not proof of a deleted video. Those source gaps
remain visible; no access restrictions were bypassed.

## Verification And Release

- Confirmed context `kubernetes-admin@kubernetes`, API `192.168.104.50:6443`, and
  namespace `keeping-law-simple` before changes.
- Backend: 340 tests passed. The optional PostgreSQL test was skipped locally and
  then passed against live PostgreSQL using rolled-back temporary tables.
- Frontend: 31 tests passed. No frontend source or dependency changes were made.
- A private canary read all 19,255 official IDs and processed three real bills
  through temporary PostgreSQL tables. No public records were written by that
  test. An initial PostgreSQL parameter-type bug was caught and fixed before release.
- API rollout used `maxUnavailable: 0`. API 2/2, frontend 2/2, database 1/1, with
  zero service-pod restarts. Database had no waiting locks or blocked queries.
- All 124 public checks passed, including every jurisdiction page/API, Wyoming,
  Eric Barlow as one search result, and 2020 SF0139. Desktop and mobile browser
  checks passed without page errors or overflow. Search and source expansion worked.
- A newly imported federal bill passed its real public page/API checks and remained
  `noindex` with no active ad markup.
- Cold homepage response took 4.55 seconds; a repeat took 2.49 seconds. The cold
  Barlow profile API took 4.42 seconds. Warm Barlow search took 0.04 seconds.
- New API logs were clean. Earlier warnings included a departing pod's readiness
  probe, the manually launched catch-up Job, and a recovery launcher path error.
  That launcher was corrected before any row changes; its failed Job was removed.
- Temporary canaries and image build Jobs were removed. Active recovery was kept.

The API and all five CronJobs use this immutable image:

`registry.skazproconsulting.com/keeping-law-simple/web:20260926-coverage2@sha256:20850765a7aedd95967f292cc378b8418460df71845cbc901553665e75e7c60e`

## Weekly Follow-Up

The existing Saturday 5:52 AM Mountain health check now includes safe coverage and
source repairs, not just reporting. It must verify progress and completion of this
backfill, inspect failed bills, check alternate official media links, and report
unresolved source gaps separately from system faults. No schedule change was made.

Carry the federal and quality recovery Jobs forward until their real results are
checked. Remove the one-time quality ConfigMap after that Job finishes. Do not
blindly retry recordings that still fail quality or erase their failure status.

No urgent owner action is needed. An authorized alternate recording could help
resolve the remaining source gaps. Ads stay off. Credentials, Sites privacy,
public hosting, and shared GPU service settings were not changed.
