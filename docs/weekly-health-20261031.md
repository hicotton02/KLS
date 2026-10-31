# Weekly Health Check - October 31, 2026

## Overall

The website is working. Source data is not a full all-clear: Wisconsin is
unreachable, Alaska requested a rate-limit cooldown, and Wyoming still has
unusable recordings. Missing source material is not an AI failure.

## Repair

Federal source holds were waiting even after Congress published their text.
The bill inventory entry had not changed, so normal source refreshes skipped
them. A fresh source check found 12 affected bills.

The federal catch-up worker now checks at most 20 source holds per pass, with a
six-hour cooldown for each bill. It skips reserved numbers and active or held
AI work. It uses the existing source lease, verifies bill identities, checks
that rows have not changed, and updates only source fields. It preserves saved
summaries and does not reset AI retry limits. Missing text stays on source hold;
only usable published text can enter normal summary processing.

Only the federal catch-up CronJob image changed. Its five-minute schedule,
resources, and concurrency stayed the same. The public site and API were not
restarted. The deployed image is:

```text
registry.skazproconsulting.com/keeping-law-simple/web:20261031-source-holds2@sha256:bccd2d1a90a5bb37fe9777a7044703ff68710d4800d4f5125410408968541334
```

A bounded recovery Job checked 53 source holds in three passes of 20, 20, and
13. It recovered all 12 published texts, with zero source errors. Normal summary
workers then picked them up through the shared background AI queue. Their saved
results are checked separately from the source Job's success.

## Source Coverage

All 52 jurisdictions have a status row. There are no stuck running rows or known
stored-bill deficits. Wisconsin's total is unknown, not zero, and its last
successful scan is outside the weekly window. Four source failures remain:
three Alaska detail checks and Wisconsin's index.

| Area | Verified result |
| --- | --- |
| Federal Congress 119 | 19,827 official identities, stored bills, and durable inventory entries; no missing or extra identities |
| California | 5,065 of 5,065 bills; no failures; scanned today |
| Washington | All 3,760 current records checked; four older unlisted records retained; no failures |
| West Virginia | 2,777 of 2,777 bills; no failures; today's scan finished at 2:59 AM Mountain |
| Hawaii | 6,132 of 6,132 official measure-roster entries; no failures |
| Wisconsin | 2,749 stored bills retained; current coverage cannot be verified |
| Alaska | All 693 listed bills are stored, plus one older record; three earlier detail failures remain open |

The federal check read every official page and matched unique bill identities,
not a latest-N sample. It used 100 requests and ended with 19,845 requests
remaining. No federal source failures, pending inventory items, or expired
source leases were found.

Hawaii's six official measure reports each supplied a checked row count. Their
combined roster matches storage. The old SB9875 gap is not present in the current
official inventory. SB9999 remains an unlisted document file, not a roster entry.
No stored bill was deleted to improve these counts.

Wisconsin's proposal index and main document site both time out during connection
setup from production. The workstation also times out. The existing bounded
retry guard retained the bills and left coverage unverified. No login, access
restriction, or network rule was bypassed.

Alaska's earlier SB125 page responded again and matched its stored source.
A guarded, read-only verification then checked 199 bill details before HB200
returned HTTP 429. The Job stopped immediately with no automatic Job retry. No
bill, text, summary, or scan status was changed, and the three earlier failures
were not cleared without a complete check. It needs a later, rate-limit-aware
source pass, not more AI capacity.

## Summary Progress

At the opening check, federal had 19,763 validated summaries, up 56 from last
week's 19,707. There were 64 source holds, with no processing, retry, quality, or
review backlog. Federal history had grown by 143 rows to 20,048, including 17
saves in the last day. History includes revisions, not just new bills.

Wyoming still has all 8,721 validated summaries and 5,806 history rows. There
were no missing queue rows or expired worker leases. HR2829 remains complete.

The source audit found usable text for HR10918, HR10919, HR10921, HR10922,
HR10925, HR10926, HR10928, HR10929, HR10930, HR10931, HR10933, and HR10950.
The other 52 source holds contain 41 bills without usable published text and
11 reserved numbers. Those 52 were not sent to AI using a title alone.

Final readback at 6:39 AM Mountain confirmed all 12 recovered summaries saved
and validated on their first attempt, each with a matching history row. All 24
new public page/API checks passed, as did checks that HR2829 and last week's six
recovered summaries are still ready.

| Area | Valid summaries | Active or retry work | Quality or review holds | Source holds | History rows |
| --- | ---: | ---: | ---: | ---: | ---: |
| Federal | 19,775 | 0 | 0 | 52 | 20,060 |
| Wyoming | 8,721 | 0 | 0 | 0 | 5,806 |

Federal's valid count is up 68 from last week's final check. There are 155 new
history rows since then, including 29 saves in the last day and today's 12
recoveries. No leases expired and no configured bill lacks a queue row.
Two bounded queue drains used normal claims and honored existing cooldowns;
no retry timestamp or attempt limit was changed by hand. The normal scheduled
source worker also completed on the new image with no extra source requests
while those bills were cooling down.

## Wyoming Vote Reasons

All 2,645 usable recordings have complete explanation scans. The 14,354
published or curated reasons are preserved. All 101 duplicate links match valid
records for the same date and chamber. No usable recording is stuck in active
transcription or explanation processing.

There are 15 transcript quality holds, three invalid audio links, and 17
confirmed private or unavailable source results. Terminal losses are separate
from processing failures. An HTTP 403 alone is not proof of private media.

The official date and chamber indexes still repeat broken links for recordings
1979, 2512, and 2515. Fresh requests and a plausible alternate returned webpages,
not audio. A known-good recording from the same 2013 session returned real audio.
No unverified replacement was applied and no quality check was weakened.

Bill scans remain at 6,584 complete, 569 partial, 25 source-unavailable, and two
waiting for review: 2006 SF0053 and SF0089. All 7,180 bills with roll calls have
a scan row. The gaps need usable evidence, not repeated transcription attempts.

## Live Site And Systems

All 122 scheduled public checks passed, including all 52 jurisdiction pages and
APIs, Wyoming's directory, one Eric Barlow result, 2020 SF0139, HR2829,
health/readiness, robots, sitemap, and ads.txt. Twelve desktop/mobile browser
cases passed with working search and source expansion and no JavaScript errors
or horizontal overflow.

The federal API/page took about 5.0/4.5 seconds in the main sweep. A separate
browser check measured a federal page response at 7.6 seconds. The first home
page load reached document-ready in 12 seconds; a repeat took 4.5 seconds.
The final sweep passed all 122 checks again, with federal API/page times down
to 3.0/3.2 seconds. These slow paths remain a performance concern.

Both public deployments have two of two replicas ready. PostgreSQL has three of
three instances ready, no blocked queries or waiting locks, and zero replica
replay lag. GPU2 is Ready and both shared queue replicas are ready. There are no
new warnings on public or database workloads. Two Job warnings are explained by
Alaska's rate-limit stop and the replaced backup helper. Services, EndpointSlices,
ingress, and rollout state passed. All CronJob images are digest-pinned. Each API
pod has ten historical restarts, last recorded October 27 at about 11:50 AM
Mountain. Neither has restarted since then; the frontend pods have zero restarts.

API logs had no application errors. Database logs show a brief control-plane
connection delay that recovered using the cached cluster definition. Frontend
logs show invalid server-action requests, one prematurely closed asset response,
and brief analytics delivery warnings with nonzero dropped counters. Normal
browser workflows passed, and 12,481 server page events arrived in the last day.
No active failure was reproduced for those older log entries.

Today's database backup finished at 4:39 AM Mountain. WAL archiving and 30-day
retention are healthy. Both replicas stream with zero replay lag. No new restore
drill was run. Last week's two recovery/readback Jobs completed; their saved
summaries and retained records were carried forward.

## Safety And Tests

Focused tests: 67 passed. Full backend: 447 passed, with two optional PostgreSQL
checks skipped locally. The changed PostgreSQL regression also passed live using
session-local temporary tables that were rolled back. Frontend build and all
31 tests passed.

The first private canary caught a PostgreSQL parameter-binding issue. It was
fixed and tested before deployment. The replacement canary ran as UID 10001,
used a read-only normal database connection, checked real official text, and
had no Service or ingress. A first backup helper lacked its review module; it
stopped before changes. Its replacement used the deployed summary image.

Before source changes, all 64 federal held records and queue rows were saved to
PVC `keeping-law-simple-data` at
`maintenance/weekly-20261031/source-holds-before.jsonl`: 261,015 bytes, SHA-256
`1663708e8043e36e3ea27c8b7c078717a6ca4e8a7d658a3238df7c55916d75d4`.
A separate canary read it back and verified the count and hash.

Alaska's original scan row and 694 source/summary fingerprints were saved to
`maintenance/weekly-20261031/alaska-status-before.jsonl`, SHA-256
`09718e0fe0c1b19c9d04d544644be866f3b4dc3942577e31c8389f1412d65e99`.
Readback confirmed the backup and unchanged scan status after the rate-limit stop.

Disposable canaries, build Jobs, and the failed backup helper were removed using
UID/resource-version guards. Source recovery Jobs and NAS backups remain.
No production data cleanup, credential change, network change, GPU reassignment,
Sites publication, or privacy change was made. Existing dirty state-feed work
was preserved and excluded from this repair's commit. The weekly schedule is
unchanged. Detailed snapshots are in ignored `tmp/weekly-20261031-*` files.

## Remaining Work

No new credentials, GPU changes, or urgent owner action are needed. A full data
all-clear still requires Wisconsin source access, a clean Alaska pass after its
rate limit allows it, the 52 federal source texts or reserved-number outcomes,
and usable evidence for Wyoming's held recordings. Carry these gaps and the
retained recovery Jobs forward. The slower public pages also remain a watch item.
