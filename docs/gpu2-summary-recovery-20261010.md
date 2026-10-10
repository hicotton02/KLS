# GPU2 and Federal Summary Recovery

## GPU Repair

GPU2 had a loaded NVIDIA driver that did not match its updated driver library.
The node was cordoned, rebooted, checked, and returned to scheduling. The RTX
5090 now works with driver 595.99.02 and kernel 7.0.0-38-generic. Actual GPU
activity and the shared background endpoint were checked, not just node readiness.

The shared Redis service also restarted during the checks. Its saved queue data
reloaded and both queue replicas recovered. This repair did not restart Redis,
change the GPU broker, clear another project's requests, or change model selection.

## Worker Repair

The old worker waited ten minutes for each HTTP response. A busy shared queue
could outlast that wait, leaving an accepted AI request behind while KLS counted
the timeout as a failed attempt.

Summary workers now request an immediate durable queue receipt. Waiting work
keeps its current attempt number and returns after a five-minute cooldown. Each
source version, logical attempt, and prompt has a stable idempotency key, so the
worker can reuse the original draft and fact-check requests. A resumed worker
does not send duplicate AI work. Requests keep normal background priority.

Only summary workers opt in. Other AI callers keep their existing behavior.
Actual failed requests and failed content checks still stop after three attempts.
Source holds, review holds, old cooldowns, validated summaries, and source records
remain intact. Lease, ownership, source-change, backup, and publish guards remain.

`waiting` means an AI request was accepted but its result is not ready. It does
not mean a new summary was saved. A successful Job with waiting results is not
proof that the backlog is finished. Check actual saved summaries and history rows.

## Release Scope

The release image contains only the two changed worker modules and the PostgreSQL
regression check. Only the federal summary CronJob received the new image. Public
API and frontend pods, other CronJobs, schedules, and the private Wyoming profile
preview are not part of this rollout.

Image:

```text
registry.skazproconsulting.com/keeping-law-simple/web:20261010-gpu2-queue1@sha256:87172c99ab6fa43041660d71baf67e07d1c3da9c735b9e84189ffe6b3dc1a77e
```

## Checks

The focused tests passed: 30 tests. The full backend suite passed: 371 tests,
with two optional PostgreSQL checks skipped locally. The private canary passed
the new PostgreSQL claim checks with rolled-back temporary tables. Two live queue
submissions reused one job. The canary had no public Service or ingress.

A real federal worker admitted one request in 0.2 seconds and saved its waiting
state. The federal schedule was then resumed with the new digest pin. All four
scheduled workers completed their first pass without runtime errors.

At 12:24 PM Mountain time, federal work had 19,434 complete summaries, 42 waiting
requests, four older attempts still cooling down, 86 review holds, and 67 source
holds. No worker lease was expired. No new public summary or history row had yet
been saved during this repair. The shared queue was draining but still had about
2,900 requests ahead of the newly admitted work. This is not a catch-up claim.

All 120 public route checks passed, including all 52 jurisdiction APIs/pages,
the single Eric Barlow result, and Wyoming's 2020 SF0139 page/API. The federal
API initially took 12.7 seconds and its page took 9.2 seconds. A repeat check took
5.3 and 4.1 seconds. Those slow paths remain a performance follow-up, not an HTTP
failure. Public API and frontend pods were not restarted by this repair.

Source and quality holds remain separate from work waiting for GPU time. Keep
checking actual saved summaries and history rows before saying work has finished.
