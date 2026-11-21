# Weekly Health Check - November 21, 2026

## Overall

The public site is working. Arkansas was repaired. West Virginia's source is
unreachable from production, so this is not a full source-data all-clear.
Missing official text and unusable recordings are separate from AI failures.

## Live Checks

- Confirmed `kubernetes-admin@kubernetes`, control plane `192.168.104.50`, and
  namespace `keeping-law-simple` before changes.
- Both public sweeps passed all 120 checks across all 52 jurisdictions. They
  covered the homepage, health, readiness, every area API and live area page,
  Wyoming, its voting directory, Eric Barlow, and 2020 SF0139's page and API.
- Desktop and mobile checks passed with no browser errors or horizontal
  overflow. Barlow returns one person. SF0139 has its plain-English summary.
- Frontend and API remain 2/2 ready each, with no restarts or fresh errors.
  All three database instances are ready. No blocked queries, waiting locks,
  or storage pressure were found. Database use is about 8.1 GB.
- Services and EndpointSlices have ready backends. The current Gateway route
  attachments are accepted and resolve their references. There are no Ingress
  objects; Gateway HTTPRoutes serve the site.
- All eight CronJobs are enabled with digest-pinned application images. The
  daily source scan still runs 32 jurisdictions at once. Schedules were not
  changed. Routine workers have no stuck leases or leftover active batch pods.

The sitemap takes about 3.0-3.5 seconds. A first Mississippi API request took
5.7 seconds; repeats took 0.27-0.44 seconds. Homepage requests took about 1.1
seconds on repeat. First browser loads, including assets, took 5.8-6.6 seconds.
No public timeout or server error was found.

## Arkansas Repair

The official bill search switched its default to 2027. Our 2026 lookup relied
on that default and had failed since November 12. It also keyed list entries
by bill number alone, which could hide a special-session bill with the same
number as a fiscal bill.

The adapter now reads the official session selector and requests each session
for the actual legislative year. It keeps fiscal and special sessions separate,
uses parsed link parameters, and rejects wrong sessions, duplicate identities,
empty advertised lists, and broken pagination.

Eight focused tests passed. The full backend suite passed 489 tests, with two
optional PostgreSQL tests skipped locally. Both optional checks then passed
against PostgreSQL using rolled-back temporary tables and the checkout's
modules. A first integration probe used the older public API module and stopped
at a missing worker-only function; no public tables were changed.

A private, read-only canary verified every listed bill: 279 fiscal bills and two
special-session bills. It checked the new bills' official detail pages too.
Before recovery, the affected bill and scan rows were backed up on the existing
shared storage. Their checksum matched the canary snapshot. Recovery checked
281 bills, added only the two special-session bills, skipped all 279 unchanged
bills, and reported zero failures. Existing IDs, summaries, source paths, and
creation dates were preserved. Both fiscal and special HB1001/SB1 pages and
APIs return the right records in production.

Only the live daily source CronJob image was patched. A version check protected
the patch, and readback proved its command, limits, schedule, and other settings
were unchanged. No frontend/API rollout or public downtime was needed. The
worker overlay preserves the other live state adapters:

```text
registry.skazproconsulting.com/keeping-law-simple/web:20261121-arkansas-sessions1@sha256:ac79b29905fa2393135e17a715c600f28729ae532d45fd40531c5624d9456a04
```

The first recovery setup stopped before database writes because its backup was
on temporary storage. The corrected Job mounted shared storage, verified the
backup, and completed. The failed setup and West Virginia attempt are retained
as evidence and have a one-day expiry; neither is an active worker.

## Source Coverage

All 52 scan rows are present. After the Arkansas repair, none are older than
seven days. West Virginia is the only failed scan and the only unknown source
total. No known source-versus-storage deficit remains. Unknown is not zero.

| Area | Result |
| --- | --- |
| Arkansas | 281/281 official identities; repaired today |
| Federal, Congress 119 | 20,203 official identities, inventory entries, and stored bills |
| California | 5,065/5,065; scanned today, no failures |
| Washington | 3,760 listed records; all stored, four older records retained; no failures |
| Wisconsin | 2,749/2,749; scanned today, no failures |
| Alaska | 693 listed records; all stored, one older record retained; no failures |
| West Virginia | Last successful scan November 19; current official total cannot be verified |

Federal verification read every official catalog page and compared identities,
not just the latest bills. It found 30 new records absent from the earlier scan.
The audit used 103 requests and ended with 19,898 requests of quota remaining.
A bounded recovery refreshed the catalog and 100 source records, with no
failures. All 30 new bills were stored. Six remaining metadata updates were
carried into a second bounded Job rather than called complete early.
That second Job completed all six with zero failures. Final source coverage is
20,203/20,203, with zero pending updates, zero failed items, and a released lease.

West Virginia's bounded recovery also failed at connection setup. Direct HTTPS
checks of both official hostnames timed out. Existing bills were retained, and
no retry limits, network rules, credentials, or access restrictions were changed.
It needs restored official-source access or a separately approved backup source.

## Summaries

Wyoming has 8,721 validated summaries out of 8,721 bills. All its queue rows are
complete, with 5,806 history entries. No new work was needed.

At opening, federal had 20,033 validated summaries and 140 source holds.
After adding the new bills, 168 awaited usable official text. The normal federal
worker then recovered text for HR11037, leaving 167 source holds, including 11
reserved bill numbers. The other 156 are source gaps, not AI failures. Three
bills entered the shared background queue: HR11130, HRES1674, and HR11037.
Their attempt counts and normal cooldowns were preserved.

A read-only check of the shared queue ledger found five successful KLS AI
requests, each completed on its first attempt in about 9-14 seconds. No shared
queue congestion or GPU failure was found. Drafting, result pickup, and the
separate fact-check pass span scheduled worker runs. Pending result pickup is
not a saved summary or a stuck processing lease.

At 6:25 AM Mountain, HR11130 and HRES1674 were saved and validated on their first
attempts, each with a saved history entry. HR11037 is still in its normal
fact-check/result-pickup cycle. Final federal counts are 20,035 validated,
167 source holds, and one waiting job. No pending, processing, retry, quality,
or review holds remain. This is moving work, not a full summary all-clear.

Federal history has 20,663 saved entries, including 89 in the last day. The last
available saved comparison is November 7, not last week: validated federal
summaries rose from 19,813 to 20,035, and history grew by 467 from 20,196.
No saved November 14 baseline was found. History also includes revisions.
There are no quality holds, review holds, failed summary retries, or expired
worker leases. A successful source Job does not mean its summaries are saved.

## Wyoming Vote Reasons

- 2,645 available recordings have finished reason extraction. No transcription
  or explanation processing is stuck. All 101 duplicate links have valid
  same-date, same-chamber parent records.
- 15 recordings remain on speech-quality holds. They produced too little usable
  timestamped speech. Quality checks were not weakened or rerun without evidence.
- Three official audio links remain unusable. The current chamber indexes
  repeat them; bounded download checks return HTML, not audio. The plausible
  corrected 2013 Senate filename also returns HTML. No wrong-date or wrong-
  chamber substitute was used.
- 17 previously confirmed unavailable/private videos remain terminal source
  gaps, not active failures. No HTTP 403 was reclassified as deleted or private.
- Bill scans: 6,584 complete, 569 partial, 25 source-unavailable, and two requiring
  review: 2006 SF0053 and SF0089. Another 1,541 bills have no recorded roll call
  and are not missing roll-call scan tasks.
- Published floor reasons remain intact: 14,351 publishable and three curated.
  Four curated news reasons remain published. No unreviewed news reason was
  released. One Wyoming Public Media topic poll remains on source hold because
  it lacks a usable article body; a fresh fetch confirmed the same problem.

## Follow-Through

The original configured checkout and the older recovery checkout have broken
Git links. Their files were left untouched. This run used a clean main-branch
checkout and verified the `hicotton02/KLS` remote. Only this repair, its tests,
image recipe, image pin, and this report are committed. Other live manifest
differences were preserved; the whole old manifest was not applied.

The weekly automation schedule and all Sites privacy settings are unchanged.
No owner action is needed for Arkansas. West Virginia's access blocker and the
remaining official-source gaps must stay visible in later checks.
