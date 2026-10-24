# Weekly Health Check - October 24, 2026

## Overall

The public site is healthy. The data is not a full all-clear. West Virginia's
official server is timing out, one Hawaii measure page is missing, and some
Wyoming recordings still cannot support a published reason.

## Public Site And Systems

- All 122 public checks passed, including all 52 jurisdiction pages and area
  APIs, health/readiness, Wyoming, Eric Barlow, 2020 SF0139, and federal HR2829.
  Twelve more checks confirmed the six recovered summaries on their real pages
  and APIs after they were saved.
- Twelve desktop/mobile browser cases passed. Barlow returns one person. Search
  and source expansion work, with no browser errors or horizontal overflow.
- The API and frontend each have two of two replicas ready, with zero restarts
  across their four pods. All Services and EndpointSlices have ready targets.
- PostgreSQL has three of three instances ready. Both replicas stream with zero
  replay lag. There are no blocked queries or waiting locks. GPU2 is Ready.
- No recent Kubernetes warning events or API application errors were found. One
  frontend logged six brief analytics delivery warnings over 24 hours; no events
  were dropped in those warnings. This was not a page outage.
- Database logs contain three peer-auth failures caused by this check's initial
  local probe using the wrong role. Corrected local-role probes passed on all
  three instances, and application database connections also passed. No
  authentication settings were changed.
- Today's database backup finished at 4:42 AM Mountain. WAL archiving and the
  30-day retention policy are working. No new restore drill was run this week.
- Every CronJob image is digest-pinned. Schedules and concurrency are unchanged.
  Old recovery Jobs were checked rather than assumed complete. Hawaii's prior
  recovery remains failed on the same missing measure page, not an active retry.
  Finished batch pods belong to retained Jobs; no stray running helper was found.

The federal area API took 2.9-4.9 seconds on repeat checks. The sitemap took
2.9-5.7 seconds. Oklahoma's first 5.6-second result fell to 0.45-0.52 seconds on
repeat checks. These are slow paths, not failed requests.

## Source Coverage

All 52 jurisdictions have a scan within the weekly window. A recent scan date
does not mean every bill was read successfully.

| Area | Live result |
| --- | --- |
| Federal, Congress 119 | All 19,775 official identities match stored bills and the durable inventory; zero missing/extra identities or pending/failed source items |
| California | All 5,065 official bills checked today; zero failures |
| Washington | All 3,760 currently listed bills checked today; zero failures; four older, unlisted records preserved |
| Virginia | All 3,646 official bills checked; zero failures; prior recovery confirmed complete |
| Hawaii | 6,132 of 6,133 stored; SB9875 still has official text but no working measure details page |
| West Virginia | All 2,777 listed bills are stored; today's detail refresh is still active and hitting source timeouts |

The federal check read every official page: 19,775 unique identities and a stable
official total, not a latest-N sample. The API reported 19,912 requests remaining
after that check. No current rate-limit failure was found.

West Virginia's last completed scan was October 22 at 3:16 AM Mountain. At
6:08 AM today, its worker had seen 1,092 bills and recorded nine failures. By the
6:16 AM readback, failures had reached 11. Logs show one HTTP 503 and repeated
read timeouts, including HB4001-HB4010. A separate
production-network request also timed out. SB873 responded on a later probe.
The worker is advancing, not stuck on a database lock, but the publisher is
blocking a clean refresh. Its existing 23-hour deadline bounds the run. No second
overlapping full scan was started, and its failures were not cleared by hand.

Hawaii's first daily pod timed out on the source list. Its replacement completed
the scan with the same one-bill gap. SB9875's official text is readable, but its
measure page still says it was not found. It was not discarded as a fake bill,
and no missing sponsor, status, or legislative action was invented.

## Summaries And Recovery

At the opening check, federal had 19,701 validated summaries and 74 source holds.
Wyoming had all 8,721 validated summaries, with no summary backlog. HR2829 remains
complete and its real public page and API work.

Federal had saved 143 history rows since the October 17 post-repair check,
including 12 in the last day. Valid federal summaries increased by 60 from that
post-repair count. History includes revisions, not just new bills. Wyoming's
5,806 history rows were unchanged; its earlier 19 held summaries remain complete.

A fresh official-source check covered all 74 federal holds. Six now have usable
published text: HR10847 and HRES1629-HRES1633. The other 68 are source gaps:
57 have no usable official text or summary, and 11 are reserved bill numbers.
None of those 68 were sent to AI using a title alone.

Bounded Job `kls-weekly-20261024-source-text` recovered the six official texts.
It uses the deployed federal summary image pinned to digest
`sha256:4aa8372568f5e2810763738d3e4269c70a1b0996f39446717811dbb9b730747c`.
Before changing a row, it acquired the normal federal source lease and backed up
all six records and queue rows to the persistent NFS volume. Each source update
checked the original row again and preserved existing interpretations. Summary
generation uses normal durable claims, shared background AI requests, evidence
checks, cooldowns, and retry limits. No held attempt limit was reset.

The backup is on PVC `keeping-law-simple-data` at
`maintenance/weekly-20261024/federal-source-before.jsonl`: six records, 21,714
bytes. A separate read-only Job read it back and verified its SHA-256:
`9ea00d439a03ad9351759f790496f6cebed59e877fb245c82d94041b1dee7c64`.
Both Jobs completed and retain eight days of evidence. A successful source Job
alone is not proof that the six summaries have been saved.

At 6:15 AM Mountain, the normal four-worker federal schedule saved and validated
all six summaries. Each completed on its first attempt, with a matching history
row. All six public pages and APIs then returned the saved summaries.

| Area | Valid summaries | Waiting/processing/retry | Quality/review holds | Source holds | Saved history rows |
| --- | ---: | ---: | ---: | ---: | ---: |
| Federal | 19,707 | 0 | 0 | 68 | 19,905 |
| Wyoming | 8,721 | 0 | 0 | 0 | 5,806 |

Final history counts show 149 federal saves since the October 17 post-repair
check, including 18 in the last day and six in this recovery. The validated
federal count is up 66 from that post-repair baseline, or 156 from the opening
count in last week's report. There are no missing queue rows, expired leases,
pending claims, or held cooldowns. The shared AI queue is ready and has no
reported waiting depth. Source coverage, saved summaries, and unavailable
evidence remain separate measures.

## Wyoming Vote Reasons

All 2,645 usable recordings have complete explanation scans. No active
transcription or extraction work is stuck. All 14,354 published/curated reasons
are preserved, and all 101 duplicate links match a valid date and chamber.

There are 15 transcript quality holds, three invalid audio sources, and 17
confirmed private/unavailable source results. Terminal losses are not counted
as active queue failures. A public 403 is not proof of private or deleted media.

Fresh official date/chamber indexes still publish the broken links for recording
IDs 1979, 2512, and 2515. Those URLs and plausible alternate filenames returned
webpages, not audio. A working recording from the same 2013 session returned real
audio, so this was not a blanket network failure. No unverified replacement was
applied, and rejected transcripts were not blindly retried.

Bill scans remain at 6,584 complete, 569 partial, 25 source-unavailable, and two
waiting for review. The review bills are 2006 SF0053 and SF0089. Existing
transcripts, reasons, and speech/evidence checks remain intact. All 7,180 bills
with roll calls have a scan row; no bill is missing from that scan inventory.

## Tests And Scope

Backend: 414 tests passed; two optional PostgreSQL checks skipped locally.
Frontend: build passed and all 31 tests passed. The one-time recovery script
passed a syntax check and live guarded execution. No application code changed,
so no new application image or public rollout was needed.

Confirmed `kubernetes-admin@kubernetes`, API `https://192.168.104.50:6443`, and
namespace `keeping-law-simple` before changes. The checkout remote is
`https://github.com/hicotton02/KLS.git`. Public deployments, credentials, GPU
policy, network rules, Sites privacy, and the weekly schedule were not changed.
Unrelated dirty work was preserved. Detailed evidence is in ignored
`tmp/weekly-20261024-*` and `tmp/held-20261024-public.json` snapshots.

No owner action is needed for the working site. A full data all-clear still
requires the source publishers to supply usable missing material and a clean
West Virginia refresh. Carry these gaps and both recovery Jobs into the next
weekly check; verify saved output rather than Job status alone.
