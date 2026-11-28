# Weekly Health Check - November 28, 2026

## Overall

The public site and database are working. Federal bill coverage is complete.
North Carolina was recovered. New Mexico and West Virginia have blocked
official sources, so this is not a full data all-clear.

## Public Site

- Both public sweeps passed all 120 checks across 52 jurisdictions. This included the home
  page, health, readiness, every live area page and valid area API, Wyoming's
  voting directory, Eric Barlow, and the 2020 SF0139 bill page and API.
- Barlow returns one person. SF0139 has its plain-English summary. Desktop and
  mobile checks passed with no browser errors or horizontal overflow.
- The sitemap took 3.1 seconds. Two cold browser home-page loads took about
  13.2-13.5 seconds; repeats took about 3.6 seconds including scripts and
  prefetches. Another fresh browser load received the page in 1.8 seconds and
  finished loading in 2.0 seconds. The regular home-page HTTP check took
  1.1 seconds. The cold-load delay is intermittent; its cause is not confirmed.
  It is carried forward as a slow path, not called fixed.

## Workloads And Database

- Verified context `kubernetes-admin@kubernetes`, API `192.168.104.50`, and
  active namespace `keeping-law-simple` before changes.
- Frontend and API each have two desired, updated, ready replicas. Their pods
  have no restarts. All three PostgreSQL instances are ready. The primary had
  one clean container exit on November 24, not a current crash loop.
- Database size is about 8.5 GB. No blocked queries, waiting locks, or storage
  pressure were found. The database operator reports healthy replication,
  working archiving, and a successful November 28 backup.
- Services and EndpointSlices have ready backends. The active Gateway routes
  are accepted and resolve their references. No Ingress objects are used.
- All eight CronJobs are enabled, use pinned application image digests, and
  keep their existing schedules. Daily feed parallelism remains 32.
- A federal source Job stopped at 3 AM Mountain when the official catalog
  changed during pagination. Later Jobs completed. The full independent
  identity check now passes, so that retained failed Job is not a stalled
  backlog. No source or summary worker lease is stuck.
- Bounded API and database logs showed no application errors. Earlier frontend
  logs contained unknown server-action references, one interrupted asset
  stream, and one analytics delivery queued for retry with zero dropped events.
  Normal page checks did not reproduce a browser error or public server error.
  These log entries do not establish a framework bug; no speculative frontend
  change was made.

The first audit's local socket readiness probe used the application database
user and caused one peer-auth rejection per database instance. This was a
diagnostic mistake, not an application outage. The final readiness probes used
the PostgreSQL user and passed. Fresh ten-minute frontend, API, and database
logs contain no errors. No database credential or authentication rule changed.

## Source Coverage

All 52 scan rows exist. At opening, no scan was stuck and no known
source-versus-storage deficit remained. North Carolina, New Mexico, and West
Virginia had failed scans and unknown current totals. Only West Virginia's
last success was older than seven days. Unknown totals are not zero gaps.
After recovery, 50 feeds are healthy, two have unknown current totals, none is
stuck running, and no known stored-bill deficit remains. West Virginia is still
the only last-success timestamp outside the seven-day window.

| Area | Verified Result |
| --- | --- |
| Federal, Congress 119 | 20,228 official identities, inventory entries, and stored bills; no missing or extra identities |
| Wyoming | 335/335 current-session source records; scanned today |
| California | 5,065/5,065; scanned today |
| Washington | 3,760 current listed records, all stored; four older records retained |
| Wisconsin | 2,749/2,749; scanned today |
| Alaska | 693 current listed records, all stored; one older record retained |
| North Carolina | Full recovery checked 2,342/2,342 bills; zero failures; finished 6:52 AM Mountain |
| New Mexico | Last success November 26; official hostname does not resolve |
| West Virginia | Last success November 19; official source connection times out |

Federal verification read every official catalog page and compared bill
identities, not just totals or the latest bills. The source stayed stable
during the check. It used 99 requests, with 19,901 requests of quota left.
No federal source items were pending, failed, missing from storage, or due for
retry. The resumable source lease was released.

New Mexico's configured official `www.nmlegis.gov` hostname fails in production,
on the control plane, and on the workstation. Google's public DNS lookup also
returns NXDOMAIN for the hostname's Check Point CNAME target. The apex hostname
resolves but fails its TLS handshake, so it was not substituted. No DNS,
security, or access-control setting was bypassed. The official feed needs
working source access; its existing records were retained.

West Virginia's official bill-list connection still times out. An independent
web fetch also timed out. No LegiScan or other unofficial backup was added,
per the owner's official-sources-only rule. These two feeds need restored
official-source access or a verified official replacement.

## North Carolina Repair

The nightly scan stopped on an HTTP 503 from the official bill index. A later
probe succeeded. The index request now uses the existing retry helper with a
three-attempt limit. It respects Retry-After, including on HTTP 429, and does
not retry HTTP 403 or 404. Persistent failures remain visible.

Ten focused retry and adapter tests passed. The full local suite passed 516
tests, with three optional PostgreSQL tests skipped locally. Those three
checks then passed against PostgreSQL using the checkout's code, session-local
temporary tables, and rolled-back transactions. Public tables were not changed
by the tests.

A uniquely tagged worker image changes only the North Carolina adapter on top
of the current live daily worker. A private read-only canary matched all 2,342
official bill identities to storage and verified six House/Senate detail pages.
No public Service selects this canary. The full source-only recovery backed up
the affected bill and sync rows on shared storage before starting. The full
scan finished after 46 minutes, checked all 2,342 bills, and found all unchanged.
It had zero failures and independently rechecked the official identities after
completion. No bill IDs, creation dates, summaries, or source records were lost.
The saved completion receipt confirms the sync row is no longer running, has
zero failures, has matching 2,342 source/stored totals, and has today's success
time. The backup and receipt are on the existing shared volume:

```text
/archive/backups/weekly-20261128-nc-before.json
/archive/backups/weekly-20261128-nc-result.json
backup SHA-256: b1ff68c8a59db71f65fce4053e1940983e97e38e6e492db1bca991bc585a3323
```

Only the live daily CronJob's image was patched, using resource-version and
old-image checks. Readback proved all other live settings stayed unchanged.
The older full repository manifest was not applied. The worker used the new
image successfully before promotion. No frontend/API rollout or public downtime
was needed; both remain 2/2 ready with zero pod restarts.

```text
registry.skazproconsulting.com/keeping-law-simple/web:20261128-nc-retry1@sha256:4b3069c1d1dbfa2e0f09f28f82631f5de025480f371db9579ae855943c0542a0
```

## Summaries

Wyoming has 8,721 validated summaries out of 8,721 stored bills. Its queue is
complete and its 5,806 history rows are unchanged. No generation is needed.

Federal has 20,160 validated summaries out of 20,228 bills, up 123 from the
November 21 final check. History grew from 20,665 to 20,841 rows: 176 new saved
entries, including revisions. Seven were saved in the last day. The latest
save was 3:25 AM Mountain today. HR2829 remains complete. S5767 is complete and
now uses the newly published official bill text, rather than relying on the
earlier Congressional Record fallback.

The remaining 68 rows are source holds: 57 lack usable official text and 11
are reserved bill numbers. All have zero AI attempts. The 57 text gaps were
checked within their six-hour source cooldown; none was overdue. HR10768 and
HRES1378 remain source gaps. Missing source text is not an AI failure.

There are no pending, processing, retry, waiting, quality-hold, review-hold,
missing queue rows, or expired summary leases. The enabled federal and Wyoming
summary CronJobs continue to use the shared background queue. The saved history
proves generation and validation are working, not just feed freshness. The
current federal backlog is blocked on source publication, not queued AI work.
Individual held bill IDs and latest source checks are listed in
[the source-hold list](federal-source-holds-20261128.csv).

## Wyoming Vote Reasons

- All 2,645 available recordings have completed reason extraction. No media,
  transcription, or explanation processing is stuck. All 101 duplicate links
  have valid same-date, same-chamber parent records.
- Fifteen recordings remain on speech-quality holds. The five November 21
  full-recording recovery receipts were read from shared storage; all failed
  quality checks and saved no candidate speech segments. Their backups remain.
  They were not rerun or published, and quality checks were not weakened.
- Three official audio links remain unusable. Fresh same-date/chamber indexes
  still point to them. Bounded checks of those links and the plausible corrected
  2013 filename return HTML, not audio. No wrong recording was substituted.
- Seventeen previously confirmed unavailable/private videos remain terminal
  source gaps, not active processing failures. No 403 was called deleted or
  private, and no login or access limit was bypassed.
- Bill scans remain 6,584 complete, 569 partial, 25 source-unavailable, and two
  requiring review: 2006 SF0053 and SF0089. Those need usable official speech.
- Published floor reasons remain 14,351 publishable plus three curated. Four
  curated news reasons remain published. Three article review labels point to
  these already-published reasons, not new candidates awaiting approval.
- One Wyoming Public Media topic poll remains on source hold. A fresh fetch
  still lacks a usable article body. No queue lease is expired or retry loop
  is spinning on it.

## Follow-Through

This run uses the clean main-branch checkout at
`C:\Users\keven\Codex\KLS-health-20261121`, with the verified
`https://github.com/hicotton02/KLS.git` remote. Older broken checkout links and
unrelated files were left untouched. The weekly schedule and Sites privacy
settings are unchanged.

New Mexico and West Virginia still need working official sources. The federal
text gaps and Wyoming recording holds remain separate, visible limits. This
report does not call those backlogs caught up.

No account setup or paid service is needed from the owner to keep KLS running.
The New Mexico and West Virginia site operators need to restore access, or we
need a verified working official replacement. The intermittent cold home-page
delay also remains open for follow-up; no unproven fix was deployed.
