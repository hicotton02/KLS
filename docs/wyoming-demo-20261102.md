# Wyoming Demo Check

November 2, 2026. Checked against production, not just a local preview.

## Demo Path

1. Open [Wyoming](https://www.keepinglawsimple.org/area/wyoming).
2. Search for [SF0101](https://www.keepinglawsimple.org/area/wyoming/bill/2026/SF0101).
   Show its summary, recorded votes, and published reasons. Art Washut, Pam
   Thayer, and Elissa Campbell are the checked examples about wording and
   law-enforcement concerns. Their source links open the official House video
   from March 5, 2026 at the cited times. The public video metadata and official
   PDF links responded normally. This is not a guarantee of playback on every
   browser or network.
3. Open [Eric Barlow](https://www.keepinglawsimple.org/area/wyoming/legislators/wy-1995).
   Show the recent voting pattern, then change the session to 2020. The earlier
   session must show House/Representative/District 3, not his current Senate title.
4. Open [Scott Smith](https://www.keepinglawsimple.org/area/wyoming/legislators/wy-2093).
   Expand the latest votes and follow a published reason to its source.
5. For history, open [2020 SF0139](https://www.keepinglawsimple.org/area/wyoming/bill/2020/SF0139).
   Expand the official summary and recorded votes.

Zero active bills in the current list reflects the recorded outcomes of these
335 bills. It does not mean the feed stopped.

## Verified Data

- Latest source scan: November 2 at 2:19 AM Mountain. All 335 current bills are
  stored, with no reported feed failure.
- All 8,721 stored Wyoming bills have validated summaries. The durable summary
  queue has 8,721 complete records and no pending, processing, retry, or held work.
- All 283 current bills with recorded votes have complete reasoning scans.
- All 2,645 usable recordings have complete explanation scans. The 14,354
  published/curated reasons and 892,823 stored member votes were preserved.
- All 335 current bill pages/APIs and 97 stored voting-record pages/APIs passed,
  along with all 22 stored years, search, filters, health/readiness, and invalid
  route checks. This was 925 checks, repeated after the main backend repair.
  No failures; the repeat sweep had no responses over three seconds.

## Repairs

- Global Wyoming search took 25 to 27 seconds because the database read bills
  in year order and rebuilt search text for many unrelated records. Matching
  now uses the existing full-text index before sorting. Five live-data canary
  responses matched the original JSON exactly, including limits and filters.
- Duplicate tag arrays are removed before transfer and parsing. Available tags
  and their area/year scope are unchanged.
- A surname-only current session no longer erases an already verified person's
  name and identifier. Party/district fallbacks do not cross chambers.
  Laura Pearson's 2026 profile and 11 reason records retain their known details.
  Ambiguous names are not guessed or reassigned.
- Historical profiles now use the selected session's title, chamber, and
  district. Barlow's 2020 House record no longer reads as a Senate record.
- The search form no longer blocks years before 2020.
- The Linux frontend build has a bounded two-GB Node heap. The one-off builder
  needed a larger memory limit; production resource limits did not change.

Public median response times after the main backend fix: Wyoming 0.359 s,
global search page 0.656 s, search API 0.328 s, Scott Smith's profile 0.891 s.
These are server response times, not time for every browser resource to finish.

## Limits

This is not a claim that every historical statement has been found or that every
summary has had a new human review. Historical reasoning has 569 partial scans,
25 unavailable-source scans, and two review holds. There are 15 transcript
quality holds, three invalid source links, and 17 unavailable source recordings.
No quality check was weakened to fill these gaps.

The current-year voting directory includes four unresolved surname-only source
records: Anderson, Cooper, Jones, and Olsen. They were not forced onto a person
where the historical name match is ambiguous. Do not describe the directory's
record count as the number of sitting lawmakers. The fresh official roster has
62 House members and 31 senators. The tested Barlow and Scott Smith searches
each return one identified person.

The dependency audit flags pre-existing build dependencies, including Next.js
[image optimization](https://github.com/vercel/next.js/security/advisories/GHSA-2464-fhr4-w9xw)
and [cache component](https://github.com/vercel/next.js/security/advisories/GHSA-673q-mjm6-r5xr)
advisories. The actual standalone production image uses Vinext; neither `next`
nor `sharp` is included, the application has no `use cache`, ImageResponse, or
Next image calls, and the public Next image endpoint returns 404. No framework
upgrade or lockfile rewrite was bundled into this demo repair. This is not a
general dependency-security all-clear; keep the audit for a separate review.

## Release Evidence

Target verified before changes: `kubernetes-admin@kubernetes`,
`https://192.168.104.50:6443`, namespace `keeping-law-simple`.

- API: `web:20261102-demo3-api@sha256:44eb6a071abcd1531757b34c1a7b34856603e0ce9f93401b198a00adeb4c08d2`.
- Frontend: `site:20261102-demo2-site@sha256:79392fbf660c98045d7c6214486d6ed0ae9b74cedad53239f068c4837b87e428`.
- Daily feed: `web:20261102-demo1-sync@sha256:146c98931cd1815b852e5c532b21391ba0ddf1d15279b88ac4edf8e69b87a66d`.
- Registry prefix: `registry.skazproconsulting.com/keeping-law-simple/`.
- Only image fields changed in live deployments/CronJob. Rollouts used zero
  unavailable replicas. Schedules, public routes, secrets, ads, and Sites
  privacy settings were not changed. Existing dirty feed work was preserved.
- Private API canaries cloned only three derived tables into session-local
  TEMP tables. TEMP setup required one writable transaction; all adapter and
  search checks then ran with default read-only transactions. Public tables
  stayed unchanged and all 2,080 cached member/year vote counts matched.
- Private frontend: 14 desktop/mobile cases passed, including font asset loads,
  year validation, and profile details. No public Service selected the canaries.
- The final public browser walkthrough passed 36 cases across desktop, laptop,
  and mobile. This included actual search submissions, source/vote expansion,
  one Barlow result, canonical Laura search, 2019 year entry, and the corrected
  2020 House/District 3 profile label. No page errors or horizontal overflow.
- Both public deployments finished at 2/2 ready with zero new pod restarts,
  no fresh application errors, and no warning events on their pods.
- Working-checkout backend: 477 passed, two optional checks skipped. Clean
  staged repair snapshot: 458 passed, two skipped. Frontend build and all
  32 frontend tests passed. The new PostgreSQL paths were tested against live
  data in the private canaries.

Derived-cache backup on the persistent NFS maintenance volume:
`/backups/maintenance/demo-20261102/legislator-cache-before.jsonl`, 741,282 bytes.
SHA256: `d38eefa92d88a1ae9a6b2d79396c48943af293485b326446d3c8a0642ece0f95`.
A separate read-only readback verified the checksum, unchanged source-table
count/timestamp snapshots, and unchanged vote totals after the repair.

The first private canary stopped at PostgreSQL's TEMP-DDL read-only restriction;
the first backup helper lacked a module; the first frontend builder hit its
memory limit. These all stopped outside public traffic. Their corrected checks
passed before the corresponding rollout. A direct debug tunnel was blocked by
the existing network policy; authorized Kubernetes port forwarding was used
instead. No network policy was changed.

Private test pods were removed using UID/resource-version checks after tests;
the local debug tunnel was closed. Public workloads, stored records, and prior
source-recovery Jobs were left intact.

Detailed reports and screenshots are in ignored `tmp/demo-20261102-*` artifacts.
