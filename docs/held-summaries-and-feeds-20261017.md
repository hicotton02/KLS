# Held Summaries And State Feed Repairs - October 17, 2026

## What Was Wrong

Some summary failures were output-size failures, not rejected facts. In a private
reproduction, Wyoming's 2015 SF0050 and 2005 HB0019 produced complete drafts, but
the fact-check step stopped at 900 tokens in the middle of its JSON. The old
worker recorded all ValueErrors as a generic validation failure. Federal HR10329
also produced a usable result during the private review.

The draft limit is now 1,400 tokens and the fact-check limit is 2,000. A response
that reports it was cut off is rejected even if its partial JSON looks valid.
Output-limit errors, incomplete JSON, and failed content checks now have separate
messages. No source or evidence rule was weakened. All three private test bills
completed both steps with the repair.

California's search page returns a Cloudflare browser challenge and HTTP 403.
The repair does not solve or bypass that challenge. It reads the state's
[official public download feed](https://downloads.leginfo.legislature.ca.gov/)
instead. Only four small metadata tables are read from the full daily ZIP using
HTTP range requests, not the entire 1.1 GB download. The adapter checks the
snapshot's version before and after reading, rejects stale or mismatched data,
and handles ordinary bills, special sessions, resolutions, and reorganization
plans. The preflight matched all 5,065 stored California bill identities.

Washington's old feature-data API returns HTTP 500 after a database query times
out on the state's server. The new adapter uses the documented
[year lists](https://wslwebservices.leg.wa.gov/LegislationService.asmx?op=GetLegislationByYear),
the not-yet-introduced list, and
[bulk bill details](https://wslwebservices.leg.wa.gov/LegislationService.asmx?op=GetLegislationIntroducedSince).
It reads both years of the biennium, keeps current versions, and rejects
mismatched bill identities. Titles, sponsors, actions, dates, and chapter numbers
come from official detail records, not bare bill numbers. Bulk reads replace
thousands of separate detail requests. The inventory took about 12 seconds in
the production-network canary.

Initiative display numbers such as `SI IL26-001` use the numeric API form for
sponsor requests. House and Senate records with the same number stay separate.
The live canary checked HB1014 and both chamber versions of initiative 26001.

## Controlled Review

All 110 selected review holds were approved for one controlled retry:
91 federal and 19 Wyoming. Their pre-change records are now saved on the NAS,
as described below. Attempts stayed at three. Source holds were not
included. Cooldowns, ownership checks, source-version checks, history, and valid
summaries remain protected. A failed result goes back to review hold and cannot
start another automatic attempt.

`scripts/review_held_summaries.py` is dry-run by default. Applying a review requires
explicit bill IDs and a new backup file. It skips valid summaries, changed
sources, missing text, active owners, and future cooldowns. The backup is flushed
to durable storage before a queue row is changed. Reusing the same backup filename
fails rather than approving the same batch again.

The first approval Job lacked a writable backup volume. Its guard stopped it
before any row changed. Replacement Job `kls-held-20261017-review-backed` wrote
its backup and completed all 110 approvals. A later volume check found that the
source worker's `/data` mount had become `emptyDir`, not a PVC, after the HA
migration. Its backup files disappeared when those completed pods were removed.
That was a backup-storage error, not a production database rollback.

The original records were recovered from the existing PostgreSQL backup and WAL
into a separate, private CloudNativePG cluster at 9:45 AM Mountain on October 17,
before these changes. Read-only exports recovered all 110 original held rows and
both state datasets. No production database was rolled back. The isolated restore
had no backup/archive-writing configuration.

Verified durable backups are now on PVC `keeping-law-simple-data`, under
`maintenance/held-20261017/`. Mount that PVC at `/backups` for future approvals;
do not assume an existing `/data` volume is persistent.

- `review-before-restored.jsonl`: all 110 original bills and queue rows.
- `ca-source-before-restored.jsonl`: all 5,065 California records before recovery.
- `wa-source-before-restored.jsonl`: all 3,764 Washington records before recovery.
- `restored-backup-manifest.jsonl`: restore target, row counts, sizes, and hashes.

Readback checked the counts and SHA-256 hashes against the manifest:

```text
review: 71b169398559a7ff9a1b67d8520fa398868ad8fef7ada9d58057ce522fc2ec13
ca:     7aa63f91f845f2e230a6eb386e4e1f88d71cdbc4382c143717adeca0cb757d0d
wa:     de8fde0e3c75903755eb5bdf4282c9f7c697a374eec00ee0581049c520010904
```

The review tool now checks the actual filesystem mount before reading queue rows.
In Kubernetes, it requires NFS/NFS4 and rejects temporary, nested scratch, and
unknown mounts. This passed unit tests and a live canary check. A writable path
alone is not proof of a durable backup.

The source preflight found no full bill text or validated summaries in the two
state datasets. The recovery Jobs explicitly refuse to proceed if richer data
appears before the backup, so a source-only pass cannot silently replace it.
Existing records are not deleted.

## Source Gaps That Remain

A fresh official API check covered all 54 federal source holds. Eleven are
reserved bill numbers. The other 43 still have no usable published official text
or summary. None were sent to AI using a title alone. These are source gaps, not
stalled GPU jobs.

Washington's current lists contain 3,760 unique records. Four older stored
resolutions are not returned: HR4606, SR8612, SR8647, and SR8671. Their official
detail records still exist but have no introduction date or current action.
Those four records were preserved as source gaps, not deleted or silently
counted as newly scanned bills. HCR4408 is covered by the official
not-yet-introduced list.

The earlier Virginia recovery completed: all 3,646 bills checked, no failures.
Hawaii's earlier full run completed with one remaining source failure: SB9875
has official text but no working measure details page. It was not discarded as
a fake bill. No Hawaii changes were made in this repair.

## Verification And Release

Confirmed `kubernetes-admin@kubernetes`, `https://192.168.104.50:6443`, and namespace
`keeping-law-simple` before changes. The Git remote is
`https://github.com/hicotton02/KLS.git`. Unrelated dirty Wyoming profile work was
left in its original checkout.

- Focused backend tests: 65 passed. Full backend: 413 passed; two optional
  PostgreSQL checks skipped locally. The changed PostgreSQL regression passed
  live using rolled-back temporary tables, with no public row changes.
- Frontend tests: 31 passed. All 120 public route checks passed, including all
  52 jurisdiction APIs/pages, Eric Barlow, health/readiness, and 2020 SF0139.
- Private canaries had no Service or ingress and used read-only public-data
  connections. The final image was checked running as UID 10001 with readiness
  HTTP 200. Earlier canaries proved full state identities and initiative details.
- The federal API/page took about 3.4/4.3 seconds in the public sweep. No route
  failed. Public API and frontend deployments remained 2/2 ready and were not
  restarted.

The final digest pin is:

```text
registry.skazproconsulting.com/keeping-law-simple/web:20261017-held-feeds4@sha256:122cdc5de6ec3917d770072477f8a5105940417ebcf06cc76ef8edc814248d0f
```

Only the daily source CronJob and the federal/Wyoming summary CronJob images
changed. Their schedules, resource settings, secrets, and concurrency stayed the
same. The final image also preserves the original non-root default user. The
earlier, code-identical recovery Jobs run with the unchanged UID 10001 pod policy.
The public frontend/API images, GPU broker policy, network rules, credentials,
Sites privacy, and weekly schedule were not changed.

## Completion Check

Recovery is checked through saved summaries, history rows, completed source
counts, and bounded logs, not Job status alone. Verified at 10:32 AM Mountain on
October 17:

| Work | Result |
| --- | --- |
| California source scan | 5,065 checked and updated; zero failures; complete at 10:12 AM |
| Washington source scan | 3,760 checked and updated; zero failures; complete at 10:09 AM |
| Federal held summaries | 90 of 91 saved and validated; one review hold |
| Wyoming held summaries | All 19 saved and validated; zero holds |
| Total recovered summaries | 109 saved, not merely queued |
| Federal queue | 19,641 complete; 54 source holds; one review hold |
| Wyoming queue | 8,721 complete; no pending, processing, retry, waiting, or held rows |
| Summary history | Federal 19,756, up 90; Wyoming 5,806, up 19 |
| Expired leases and blocked queries | Zero |

The remaining review hold is federal HR2829, the SERVICE Act, bill ID 6608325.
Its response still exceeds the output limit. It stays held with its original
attempt count and cooldown. It was not repeatedly retried or published from a
partial response. The 54 separate source holds remain unchanged.

Source Jobs `kls-held-20261017-source-ca` and `kls-held-20261017-source-wa` each have
one worker, a one-hour deadline, no automatic Job retry, and eight days of retained
history. Carry any unfinished result forward and verify its actual totals. Summary
processing continues through the existing five-minute schedules. Bounded Job
`kls-held-20261017-summary-drain` saved the final ten federal results using normal
queue claims and cooldowns. These recovery Jobs are retained for eight days.

After the backup readback, temporary canaries, build Jobs, and failed backup
helper Jobs were removed. Hibernating the isolated restore was blocked because
the operator could not read that private instance's status. No network policy
was widened. Instead, the isolated cluster was removed with orphan propagation,
which kept its 20 GiB restore PVC. Its temporary non-data resources were then
removed with UID and resource-version guards. The restore PVC and exported NAS
backups remain; there is no running restore database pod. The live three-instance
database and existing backup PVC are unchanged.

Final workload checks found both public deployments 2/2 ready, all four public
pods at zero restarts, the production database 3/3 ready, and no errors in the
bounded fresh frontend/API logs. All five retained recovery/export Jobs completed.

Detailed evidence is in ignored `tmp/held-20261017-*` snapshots in the repair
checkout. Backups, published reasons, transcripts, and quality checks were kept.
