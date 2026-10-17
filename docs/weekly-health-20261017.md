# Weekly Health Check - October 17, 2026

## Status

The public site is working. Two confirmed faults were fixed: database lock
collisions between summary workers, and a backup retention error. This is not a
full data all-clear. Some summaries need review or source text, two state feeds
are blocked, and two bounded source recovery runs are still working.

Checks ran from about 5:52 to 6:30 AM Mountain time. Production was checked through
`kubernetes-admin@kubernetes`, server `https://192.168.104.50:6443`, in namespace
`keeping-law-simple`. The checkout remote is `https://github.com/hicotton02/KLS.git`.

## Repairs

- Four summary workers were trying to refresh the same unchanged queue rows.
  A failed federal Job from October 16 showed `LockNotAvailable` in the queue
  refresh. PostgreSQL locks even an unchanged conflicting UPSERT row. Refreshes
  now use one nonblocking transaction lock per state and skip unchanged rows.
  Workers can still claim separate bills. Retry limits, cooldowns, quality checks,
  source holds, leases, and saved history were not reset or weakened.
- The Barman backup destination ended with a slash. Annotation listing added
  another slash, causing MinIO to reject retention requests. A guarded,
  idempotent path-only patch removed the redundant trailing slash. Existing
  backup object keys did not move. Credentials and the 30-day policy stayed the
  same. Actual retention and WAL archive cycles succeeded after the repair.
  All eight physical backups still exist and are valid. This repair deleted none.

Both summary CronJobs now use this immutable image:

```text
registry.skazproconsulting.com/keeping-law-simple/web:20261017-refresh-lock1@sha256:9af73bf1c743b8a8e3e45e2cd3b3c13e43fbbdafcf2aef078d364c03d9a84349
```

Only the summary CronJob images changed. Public API and frontend images, resources,
replica counts, and all schedules stayed unchanged. Both new summary schedules
have completed four-worker runs without fresh runtime errors. No public traffic
was sent to the private canary; it was removed after verification.

## Summary Progress

Counts below come from validated bill content, queue rows, and saved history,
not just successful Jobs or fresh feed timestamps.

| Area | Valid summaries | Waiting for review | Missing source text |
| --- | ---: | ---: | ---: |
| Federal | 19,551 | 91 | 54 |
| Wyoming | 8,702 | 19 | 0 |

Federal valid summaries increased by 117 from the prior verified count of 19,434.
Since the prior check, 182 federal history rows were saved across 179 bills;
these include revisions, not just first-time summaries. There were 43 saves in
the last day. The latest was October 17 at 1:35 AM Mountain. Wyoming's 5,787
history rows and valid summary count were unchanged.

There are no pending, processing, retry, waiting, or quality_hold summary rows,
and no expired worker leases. The federal review holds include 81 failed content
checks and 10 older timeouts that already reached their attempt limit. Wyoming's
19 holds failed content checks. They were not forced through or blindly retried.
The 54 missing federal source texts are source gaps, not current AI failures.
All configured bills have queue rows. A prior waiting sample, S5591, is complete.

GPU2 is Ready and the shared background endpoint works. The shared queue has no
waiting requests. Idle GPU time is not blocking these held records.

## Source Coverage

Congress 119 is the only configured federal Congress. A fresh official paginated
scan found 19,696 unique identities. Stored bills and durable source inventory
both match all 19,696, with no missing or extra identities. No source refresh
items are pending or failed. The check used 100 requests; 19,900 remained in the
reported API quota. This is a full identity comparison, not a latest-N scan.

All 52 jurisdiction sync rows exist. The other 48 jurisdictions had no failed
totals, no known stored-count deficit, and a successful scan within seven days.
The four exceptions are:

- California: official bill search returns HTTP 403. Its last successful scan was
  October 5, outside the weekly window. Current source totals are unknown.
- Washington: its official bill-list service returns HTTP 500. Last success was
  October 16, but current source totals are unknown. A fresh date is not proof of
  current full coverage.
- Hawaii: 6,132 of 6,133 listed bills are stored. The missing SB9875 has real
  official bill text, but its measure details page says not found. It was not
  discarded as a placeholder and no missing metadata was invented. Recovery was
  still moving at 6:25 AM: 3,700 checked, no failures so far. This is not a final
  zero-failure result.
- Virginia: all 3,646 listed bills are stored. A previously failing HJ166 source
  responded to a fresh check. A full recovery was still moving at 6:25 AM:
  1,300 checked, no failures so far. Completion is not yet verified.

Carry these recovery Jobs into the next check and verify their actual results:

| Job | Started Mountain Time | Deadline | Result at Report Time |
| --- | --- | --- | --- |
| `kls-weekly-20261017-source-hi` | 6:07 AM | 4 hours | Running, advancing |
| `kls-weekly-20261017-source-va` | 6:07 AM | 90 minutes | Running, advancing |

Each uses one source-only worker, no automatic Job retry, and eight days of
retained history. No AI work or manual counter reset was added. Do not call them
fixed until final source totals and per-bill results agree. No access restriction
or rate limit was bypassed.

## Wyoming Reasons

All 2,645 usable recordings have completed explanation scans. There is no active
transcription or extraction backlog. The 14,354 published/curated reasons remain
intact. The 101 duplicate links still point to matching valid records.

There are 15 transcript quality holds, three invalid official audio links, and
17 records with explicit private/unavailable source results. These are separate
from active processing failures. Recording 480, previously access-blocked, now
has a saved October 7 result saying no timestamped speech was returned; it is a
quality hold, not proof of a private or deleted video.

The official date/chamber indexes for invalid recordings 1979, 2512, and 2515
still repeat the broken links; fresh requests returned webpages, not audio. No
verified same-session replacement was found. Existing transcripts were kept and
speech/evidence checks were not weakened.

Bill scans remain at 6,584 complete, 569 partial, 25 source-unavailable, and two
review holds. The review bills are 2006 SF0053 and SF0089, each with two linked
recordings and none scanned. No bill with roll calls lacks a scan row. Partial
and held scans still need usable evidence; they were not endlessly requeued.

## Live Verification

- All 120 public route checks passed twice, including all 52 area APIs and live
  pages, health/readiness, Wyoming, the single Eric Barlow result, and 2020 SF0139.
- Twelve desktop/mobile browser cases passed. Search and source expansion work;
  no page errors, horizontal overflow, or active ad slots were seen.
- API and frontend each remain 2/2 ready, with zero current pod restarts. Services,
  EndpointSlices, routes, warning events, rollout state, CronJobs, Jobs, and bounded
  logs were checked. Old failed summary Job evidence was retained.
- The three-instance PostgreSQL HA cluster is healthy on three nodes. Both
  replicas stream in quorum with zero measured byte lag. Storage is about 15%
  used. There are no blocked database queries. The old StatefulSet is intentionally
  scaled to zero after the HA move; it was not restarted or deleted.
- Backup retention and WAL logs are clean after the path correction. All eight
  backups are complete and physically readable. A restore drill was not run.
- The federal page remains a slow path: about 5.1 seconds in the final HTTP sweep.
  Earlier federal API/page checks took about 3.1/3.4 seconds. No route failed.
- Frontend logs had six transient analytics delivery warnings in 24 hours, with
  one to three queued events and zero drops in those messages. This was not
  reproduced as a current outage and was not declared permanently fixed.

Focused regression tests: 42 passed. Full backend: 383 passed, two optional
PostgreSQL checks skipped locally. The changed PostgreSQL regression passed in
the private canary using rolled-back temporary tables. Four concurrent real-data
read-only refresh probes produced one leader and three immediate skips, without
changing public queue/history rows. Frontend build and all 31 tests passed; on
Windows, the build was invoked directly with the environment variable set in
PowerShell because the npm wrapper uses POSIX assignment syntax.

## Follow-Up

No urgent server action is needed. Held summaries need content review or missing
official text. California, Washington, and Wyoming archive gaps need working
source access or verified official replacements. Verify the Hawaii/Virginia Job
results at the next run before claiming those failures are cleared.

Unrelated dirty Wyoming profile work was preserved. The weekly schedule, Sites
privacy, credentials, network policy, shared GPU policy, and public hosting were
not changed. Detailed evidence is in ignored `tmp/weekly-20261017-*` snapshots.
