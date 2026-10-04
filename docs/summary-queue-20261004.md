# Wyoming and Federal Summary Catch-Up

## What Was Stalled

Source feeds were current, but no scheduled worker was filling missing
plain-English summaries. A fresh source scan did not mean the summaries were done.

Live verification used `kubernetes-admin@kubernetes`, control plane
`192.168.104.50`, namespace `keeping-law-simple`.

## Repair

- Added a durable `bill_summary_work` queue and `bill_summary_history` backups.
- Added Wyoming and federal CronJobs, each with four parallel workers.
- Runs are bounded to 32 bills per worker and a 25-minute soft deadline.
- Schedules check every five minutes and forbid overlapping scheduled runs.
- Initial kickoff batches are also bounded. They briefly overlap the first
  scheduled batches, giving 16 workers during initial catch-up and eight after
  the kickoff batches drain. All active batches were registered in CronJob
  status so further scheduled batches cannot overlap them.
- Requests use the existing shared background AI queue on port 11435. The
  shared broker controls GPU use; no GPU pool configuration was changed.
- Workers claim bills with PostgreSQL row locks and skip already-claimed work.
- Interrupted work can be reclaimed after its lease expires. Retries are
  limited to three attempts, with cooldowns and a final review hold.
- Existing ready summaries are kept. New drafts must pass the existing
  fact-check and content gates. Source or summary changes during generation
  prevent an old worker from publishing.
- Only summary, tags, search text, and update time are changed. Source records,
  votes, transcripts, and published voting reasons are preserved.
- Added summary-progress and recovery checks to the weekly health check.
  Its Saturday schedule is unchanged.

## Release

API and all seven CronJobs use this immutable image:

```text
registry.skazproconsulting.com/keeping-law-simple/web:20261004-summaries1@sha256:6a3897a451983cd2e5303b713ef3271d355b83968b384cc70011daf4aba850e0
```

The API received an image-only rolling update with zero unavailable replicas.
Existing live memory settings were preserved. The frontend image was unchanged.
The private canary had no public Service, and its test schema was removed.

## Verification

- Backend: 361 passed, two optional PostgreSQL tests skipped locally.
- Summary queue: 14 focused tests passed.
- The new PostgreSQL regression test passed against the live database using
  rolled-back temporary tables, without changing public tables.
- A private schema canary generated and saved one real Wyoming summary and
  one federal summary. PostgreSQL skip-locked claims, stale ownership guards,
  source guards, quality checks, and history backups passed.
- Frontend: 31 tests passed.
- Public routes: all 124 checks passed, including all 52 area APIs/pages,
  Wyoming legislators, the single Eric Barlow result, and 2020 SF0139.
- New Wyoming 2026 SF0081 and federal 119 HCONRES10 summaries appeared on
  their real public pages. Desktop and phone checks passed with no overflow,
  JavaScript errors, active ad markup, or model labels.
- API and frontend were 2/2 ready; PostgreSQL was ready. No pod restarts or
  blocked database queries were found. Database disk use was 18%.
- New API and summary-worker logs had no runtime errors. PostgreSQL logged
  two failed diagnostic SELECTs, which were corrected; no data was changed.
- Kickoff Jobs initially caused `UnexpectedJob` warnings; active Job references
  were registered with their CronJobs. These were launch warnings, not failed
  summaries. Confirm no new occurrences on the next health check.

## Progress at 8:28 AM Mountain, October 4

| Queue | Saved This Release | Ready Total | Pending | Processing | Quality Hold | Source Hold |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Wyoming | 17 | 2,932 | 5,780 | 8 | 1 | 0 |
| Federal | 15 | 49 | 19,197 | 8 | 1 | 284 |

There were no expired leases, backend retry failures, or final review holds.
These counts are a snapshot, not a completion claim. Both queues were moving.
The 284 federal source holds lack enough official source text or a usable source
link; they must not be replaced with invented summaries. Quality-held drafts
retry after six hours, then stop for review after three attempts.

All 52 source feeds had zero failures, no active sync, and successful scans
today. Wyoming's previously identified voting-reason source and quality blockers
remain separate from this summary queue.

Existing frontend analytics logs contain intermittent delivery losses from
October 3. No new frontend or API errors appeared during this release, but the
older analytics issue is not declared resolved by this work.

The slowest public HTTP check was the sitemap at 3.08 seconds; homepage was
2.88 seconds. Browser navigation checks, including asset loading and waiting for
network idle, ranged from 3.89 to 7.13 seconds. Those are not API timings.

## Check Progress

```powershell
kubectl exec -n keeping-law-simple deployment/keeping-law-simple-web -- python -m app.summary_queue --state wy --status
kubectl exec -n keeping-law-simple deployment/keeping-law-simple-web -- python -m app.summary_queue --state us --status
kubectl get cronjobs,jobs -n keeping-law-simple
kubectl logs -n keeping-law-simple -l app.kubernetes.io/component=summaries-wyoming --tail=20 --prefix=true --max-log-requests=8
kubectl logs -n keeping-law-simple -l app.kubernetes.io/component=summaries-federal --tail=20 --prefix=true --max-log-requests=8
```

Compare actual saved history rows and validated summaries between checks. Do
not treat a completed Job, a queue claim, or a fresh scan date as proof that the
summary backlog is finished. Keep source gaps, quality holds, processing errors,
and Wyoming media access blockers separate.
