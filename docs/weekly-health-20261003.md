# Weekly Health Check - October 3, 2026

## Status

The public site is working. Two feed faults were fixed and deployed. Utah is
caught up. All 19,539 official federal bill IDs are now stored, but updates to
older federal records are still being processed. This is not a full data all-clear.

At 6:27 AM Mountain, the federal queue had 586 records left to refresh and zero
failed bills. The last completed full federal scan was October 2. The new scan
does not advance that date until all pending source updates finish.

## Fixed This Week

- Utah's official server omitted its certificate chain. The Utah client now
  supplies the two public issuer certificates while still checking the hostname
  and requiring a chain to an existing trusted root. TLS verification stays on.
- The federal API repeated a bill on page 12 of its combined list. The client now
  falls back to the eight official bill-type lists. Each page, bill ID, type, and
  total must be valid. The combined unique count must equal the unchanged official
  total before and after the scan. Incomplete results still fail closed.
- Utah recovery checked all 1,016 official bills against stored records. All were
  unchanged, none were missing, and there were zero failures. Its scan date updated.
- Federal recovery added the 98 missing bill IDs. The full inventory increased
  from 19,441 to 19,539. Changed records also entered the durable refresh queue.

The existing request pacing, retry limits, per-bill cooldowns, and worker lease
remain in place. A source check reported HTTP 200, a limit of 20,000, and 19,209
requests remaining. No rate-limit bypass or extra concurrent federal worker was used.

The official [Congress API documentation](https://github.com/LibraryOfCongress/api.congress.gov/blob/main/Documentation/BillEndpoint.md)
lists the bill-type endpoints. The Utah issuer chain and public certificate hashes
are documented in `app/certs/utah-chain.md` and covered by tests.

## Live Checks

- Confirmed the Git remote is `https://github.com/hicotton02/KLS.git`.
- Confirmed `kubernetes-admin@kubernetes`, control plane `192.168.104.50:6443`, and
  namespace `keeping-law-simple` before changes.
- All 124 public route checks passed, including all 52 jurisdiction pages/APIs,
  health/readiness, Wyoming, Eric Barlow, and the 2020 SF0139 page and API.
- Eric Barlow appeared once. Desktop and mobile browser checks passed on six key
  pages, with no page errors or horizontal overflow. Search and source expansion worked.
- A newly imported federal bill, HR10711, returned HTTP 200 on its page and API.
  Its unfinished content remained `noindex, follow`, with no active ad slots.
- API 2/2, frontend 2/2, database 1/1. Service endpoints were ready. Ingress/routes,
  rollout state, warning events, and bounded logs were inspected.
- The new service pods had zero restarts and clean API error logs. Two old API
  pods each restarted once on October 1 with exit 137 after shutdown began.
  Kubernetes did not mark them OOM-killed. The original trigger remains unconfirmed.
- PostgreSQL accepted connections, had no blocked queries or waiting locks, and
  used 18% of its disk. Some database log entries came from corrected read-only
  audit probes using the wrong role/table/column, not from failed site requests.
- All 52 sync rows had known totals, no stored-count deficit, zero failed totals,
  and a successful scan within seven days. One row was actively running: the
  federal recovery, with a renewing lease and advancing saved work. None were stuck.
- All five CronJobs stayed enabled on their existing schedules and now use the
  new digest-pinned API image. The daily 52-area job still uses 32-way parallelism.

## Wyoming Source Gaps

The prior quality recovery was checked through the stored results, not just Job
status. All 13 selected recordings received September 26 retry results and still
failed the same quality rules. Together with recording 933, 14 recordings remain
held. They were not blindly requeued, and quality checks were not weakened.

- 2,645 usable recordings have completed explanation scans.
- 14 recordings remain held for poor transcript quality.
- Three official archive links still return HTML rather than audio. The current
  official date/chamber listings were checked; no verified same-session replacement
  was found. IDs: 1979, 2512, 2515.
- Public video 480 still has an audio-download HTTP 403. This is an access blocker,
  not proof that the video is private or deleted.
- 17 records remain classified as unavailable/private source loss, separate from
  active processing failures. Those terminal sources were not requeued.
- 101 duplicates point to valid matching date/chamber records. Prior repairs for
  1162 and 1188 remain intact; recovered recording 1116 remains complete.
- No Wyoming bill with a recorded roll call lacked a scan row. There were 6,584
  complete bill scans, 569 partial scans, two review holds, and 25 source-unavailable
  scans. The partial scans still have one to four unresolved recordings each.
- There was no pending extraction work for usable transcripts. The 14,354 stored
  publishable/curated reasons were preserved, along with existing transcripts.

No unverified media substitution, access bypass, or destructive data cleanup was made.

## Remaining Work

Federal Job `kls-weekly-20261003-federal-recovery` remains active with a 500-record
limit, a one-hour deadline, and no automatic Job retry. It was observed saving
records with zero failures. The unchanged five-minute catch-up CronJob will handle
remaining work after its lease is released. A scheduled Job that skips because
the lease is held does not prove the catch-up is finished.

Carry this Job forward. Confirm its actual result, all configured Congress totals
and identities, pending source versions, and per-bill failures before declaring
completion. The only configured Congress is 119. Stored ID coverage is complete;
refresh coverage is not yet complete.

The homepage/overview took about five seconds during the cold post-rollout check;
the sitemap took 6.3 seconds. A later homepage request took 2.86 seconds. These are
remaining slow paths, not failed routes.

Frontend logs show earlier intermittent analytics delivery failures and dropped
events. New events are being saved: 1,086 site-server events arrived in the hour
ending around 6:23 AM Mountain. The collector's old timeouts were not reproduced
during this check, so no speculative timeout or queue change was made. The exact
cause remains a follow-up item; analytics totals may undercount visits.

No urgent owner action is needed. Authorized alternate recordings or a correction
from Wyoming's archive could help close the remaining source gaps. Ads stay off.

## Release And Cleanup

Focused backend tests: 26 passed. Full backend suite: 347 passed, one optional
PostgreSQL test skipped locally and then passed against the live database using
rolled-back temporary tables. Frontend suite: all 31 passed.

A no-public-traffic canary verified all 52 area APIs, the full 19,539-ID official
inventory, Utah's 1,016-bill list, and the real text of HB0001 over verified TLS.
The API rollout used zero unavailable replicas. Live memory settings and unrelated
configuration were preserved by changing only images.

API and all five CronJobs now use:

`registry.skazproconsulting.com/keeping-law-simple/web:20261003-weekly1@sha256:d59d5923350cae9d44758cce643d9ec1b33a2b90fab231dd1198718b8201c921`

After validation, the temporary canary, completed build and Utah recovery Jobs,
and unused prior quality-recovery ConfigMap were backed up and removed. Active
federal recovery and the CronJob's bounded success/failure history were kept.
Detailed audit snapshots are in the checkout's ignored `tmp/weekly-20261003-*` files.

The weekly schedule, credentials, Sites privacy, public hosting, frontend image,
and shared GPU settings were not changed.
