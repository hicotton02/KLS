# Page Speed and State Source Recovery

Checked October 31, 2026, Mountain time. Production is serving pages normally.
The two source feeds below are **not caught up**.

## Live Results

| Route | Before | After, median of three |
| --- | ---: | ---: |
| Homepage | 1.781 s | 1.031 s |
| Federal page | 3.047 s | 0.594 s |
| Federal area API | 3.141 s | 0.578 s |
| Overview API | 2.109 s | 1.031 s |

These are full HTTP response times, not time until all browser activity stops.
All 132 public route checks passed, including all 52 valid area APIs, every live
area page, Wyoming's directory, one Eric Barlow result, SF0139 (2020), search,
filters, and health/readiness. None exceeded three seconds in that run. The
slowest was New York's area API at 1.219 s.

Desktop and mobile browser checks passed with no page errors or horizontal
overflow. The first cold homepage visit took 5.612 s to reach browser network
idle; later page visits took about 1.7 to 2.9 s. This is separate from server
response time. No layout, consent, ad, or Sites privacy changes were made.

## What Changed

- Plain browse now asks the database for only the bills shown. Previously the
  federal endpoint parsed 19,827 bills before returning 60.
- Search and tag matching still use all candidates. Counts stay complete.
- Latest-session counts avoid joining older sessions unnecessarily.
- A concurrent partial index speeds up recent-bill selection without changing
  its date checks or content checks. Production's query plan used the index;
  the measured database query took 4.624 ms. The index is valid and ready.
- Alaska waits at least three seconds between adapter requests, uses bounded
  network attempts, and saves rate-limit/access/transport cooldowns in the
  existing database breaker table. HTTP 403 stays an access hold, not proof of
  deleted media. Retry-After is not shortened.
- Successful Alaska detail reads are checkpointed for six hours in
  `alaska_source_checkpoints`. Changed catalog data invalidates a checkpoint;
  a new worker can resume matching, unexpired details. Empty catalogs, wrong
  session links, and mismatched bill identities fail closed.
- An interrupted Alaska pass stops rather than requesting every later bill.
  It no longer advances `last_success_at` after a failed check.
- `scripts.verify_alaska_sources` provides bounded, backed-up verification.
  It never writes bill rows. An unknown, smaller, changed, or duplicate catalog
  cannot clear a failure. It clears scan failures only after all official
  identities, source hashes, and stored action fields match and the original
  scan/bill rows are still unchanged. Expired checkpoints need a new source read.

## Source Blocks

**Wisconsin:** The official bill index and site root timed out from production;
the index also timed out from the workstation. Stored bills remain intact.
There are 2,749 stored bills, one reported failure, unknown official totals,
and a last successful scan of October 24. Do not treat unknown totals as zero.
The connected mailbox has an October 25 OneVote account email. No LegiScan API
approval email or API key was found. No backup source or paid plan was enabled.
API access still needs owner follow-up; a general OneVote account is not API access.

**Alaska:** There are 693 bills in the last verified official catalog and 694
stored records, including one retained older record. The morning verification
Job `kls-weekly-20261031-alaska-verify` stopped after 199 details on HTTP 429.
The new Job `kls-alaska-20261031-verify` stopped on three official catalog read
timeouts before reaching any details. It saved zero new checkpoints. The saved
cooldown ends November 1 at 03:50:07 UTC (October 31 at 9:50:07 PM MDT).
It is stopped, not an active catch-up worker. The next scheduled source scan
will use the new adapter. A further bounded verification pass can run after
the cooldown with a new backup file; do not reset the breaker to force a retry.

Three prior failed detail checks remain on the scan row. All 694 bill rows,
688 stored full texts, summaries, and the original scan row were preserved.
A read-only Job `kls-alaska-20261031-readback` compared all rows against the
durable backup and confirmed exact equality. Source availability, not AI or
GPU capacity, is blocking this recovery. Neither state's totals are a new
proof of full coverage.

## Deployment and Evidence

Context: `kubernetes-admin@kubernetes`, API `https://192.168.104.50:6443`,
namespace `keeping-law-simple`. Verified before changes.

- API: `web:20261031-pages1@sha256:8db9d8d2dc4135fedf18262df731688ab1b4456a854a065eac7a5024a7bf9063`.
- Daily sync: `web:20261031-alaska2@sha256:dcd7b6eaaf8074a4267f6f12904169e3962c9195d46d7551a18f300018b3be3e`.
- Registry prefix: `registry.skazproconsulting.com/keeping-law-simple/`.
- Private page canary used read-only production data, disabled application
  startup writes, had no matching Service selector, and returned exactly the
  same tested JSON as production before rollout. Index build was concurrent.
- API rollout changed only its digest-pinned image with zero unavailable
  replicas. API and frontend are each 2/2 ready, with zero current pod restarts
  and no errors in bounded fresh logs. No database lock waits were found.
- Alaska's private canary confirmed read-only production access and bounded
  Retry-After handling. The daily CronJob changed only its image. Its schedule,
  concurrency, and the weekly automation schedule were not changed.
- Full working-checkout backend suite: 469 passed, 2 skipped. The clean, staged
  repair snapshot also passed its full suite: 450 passed, 2 skipped. Focused listing
  tests: 42 passed. Focused Alaska/sync tests passed, with added checkpoint,
  pacing, hold, identity, and guarded-completion coverage in the full suite.
- Existing unrelated checkout changes were preserved and excluded from this
  repair's commit. The image overlays retain those already-deployed changes.

Durable Alaska backup on the existing NFS-backed maintenance PVC:
`/backups/maintenance/alaska-20261031/status-and-bills-before.jsonl`.
SHA256: `f7f97db0918ea17cd47f7378cf062bbb977f5efa621521c182fc0dcaa2e3e68d`.

Local evidence: `tmp/pages-20261031-public.json`,
`tmp/pages-20261031-browser.json`, browser screenshots, and the before profiles
in `tmp/perf-20261031-*`. Retain the two failed Alaska Jobs as source-block
evidence; do not label them completed or requeue without a bounded plan.
