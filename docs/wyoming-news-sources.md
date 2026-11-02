# Wyoming Reported Vote Reasons

The news lane supplements official records. It does not replace them.

## Sources

- Cowboy State Daily: public news sitemap and article pages.
- WyoFile: public news sitemap and article pages.
- Wyoming Public Media: public news sitemap and article pages.

The worker checks robots rules, uses its own user agent, spaces requests, limits
downloads, and follows only approved HTTPS publisher URLs. A 403 stays an access
hold. A 429 opens a publisher cooldown and honors Retry-After. It does not bypass
logins, paywalls, robots rules, or rate limits.

## Review Rules

New candidates are private until reviewed. To publish, verify:

1. The full article names the lawmaker and gives their own reason.
2. A bill number, official link, or literal official title identifies the bill.
3. The year, session, chamber, vote stage, member, and yes/no position match an
   official roll call. A later vote cannot stand in for an earlier vote.
4. The exact evidence and named attribution appear in the stored article.
5. The short public summary is a neutral paraphrase of the reason, not a guess.

Reported quotes and attributed paraphrases are labeled separately internally.
Both need review. Public pages show the publisher, date, and article link, not
the full article or stored evidence. Official floor statements keep their video
links and timestamps. Reasons can coexist; one source does not overwrite another.

## Operations

```text
python -m app.wyoming_news --stage discover
python -m app.wyoming_news --stage process --limit 6
python -m app.wyoming_news --stage status
python -m app.wyoming_news --stage review
python -m app.wyoming_news --stage review --approve ID --reviewer REVIEWER
python -m app.wyoming_news --stage process --url https://APPROVED-PUBLISHER/ARTICLE --limit 1
```

The queue stores attempts, cooldowns, worker tokens, and lease expiry. It uses the
shared background AI queue with an article-revision idempotency key. Waiting for
AI does not spend the four-attempt processing budget. Expired last-attempt leases
go to a hold. Automatic work never approves candidates or resets held items.

Article URLs are normalized for tracking parameters. Candidate uniqueness includes
the article fingerprint, bill, session, roll call, and member. Publication is
invalidated if the stored article revision changes or its official vote no longer
matches. Existing floor reasons and transcripts are never changed by this worker.

An article's `review_hold` status means extraction found candidates. Check each
reason's `review_status` to distinguish pending review from an already curated
reason; do not count an article containing approved examples as a failed job.

The initial hourly worker checks current news sitemaps, not complete publisher
archives. Older articles can be queued explicitly. An unavailable or undated
article, ambiguous bill, missing roll call, unmatched name, or unclear reason is a
gap or review hold, not proof of a lawmaker's motive. Historical coverage is not
complete. Previously stored articles are not automatically refreshed in this first
release; check the live article again when approving a candidate.

The weekly health-check schedule is unchanged. The new queue's status and held
items can be checked with the commands above.

## Initial Release: November 2, 2026

Four source-reviewed examples are published:

- [2023 HB0152](https://www.keepinglawsimple.org/area/wyoming/bill/2023/HB0152):
  Cowboy State Daily reports Forrest Chadwick's different reasons for third
  reading and concurrence. Each reason is tied to its own recorded vote.
- [2024 HB0005](https://www.keepinglawsimple.org/area/wyoming/bill/2024/HB0005):
  Wyoming Public Media reports John Bear's reason for opposing introduction.
- [2022 SF0066](https://www.keepinglawsimple.org/area/wyoming/bill/2022/SF0066):
  WyoFile reports Cale Case's reason for opposing third reading.

`scripts/seed_reviewed_wyoming_news.py` rechecks the reviewed article fingerprints,
official votes, and exact evidence. It defaults to a dry run. `--apply` performs
idempotent inserts and records the source-review identifier; changed source text
or votes stop publication.

Verification: 502 backend tests passed, 2 skipped; the clean staged snapshot passed
483 tests, 2 skipped. Frontend build and all 33 tests passed. Private PostgreSQL
checks used temporary news tables and read-only production source data. Desktop
and mobile browser checks passed for 12 private and 16 public routes, including
all three publishers and the two different votes on HB0152.
All 925 public regression checks passed, covering 335 current bills, every stored
Wyoming year, and all 97 stored current-year voting records. None exceeded three
seconds in that run. Those 97 records are not a claim of 97 sitting lawmakers.

The immutable API image is `web:20261102-news1-api` at digest
`sha256:c54bf70f36cbf7cfb5073140b51cb29dcb775b6cf46af89d9fcd56ab6ba165ac`.
The frontend is `site:20261102-news1-site` at digest
`sha256:7a4642ad2ec7fd646f8a38fff3b029f701a480d41c15c83a0166a2605b972083`.
Both public deployments retain two replicas and zero-unavailable rolling updates.
The API build preserves the prior live image's other modules and replaces only
the news storage, extraction, and serializer code. No unrelated dirty work was
deployed, committed, or reverted.

The hourly news worker uses `web:20261102-news2-worker` at digest
`sha256:c4d3755edaf5a188ac5a015c9d3a9d253cfe4fbea09a328843d62d867257c38d`.
This worker-only follow-up rejects syndicated originals outside the publisher
allowlist and narrows sitemap headlines to Wyoming/legislative coverage. It
does not follow links to unapproved originals or retry them as processing errors.
Run news maintenance from this checkout or that worker image, not an older API
image. Existing recordings and the public API did not need another rollout for
this discovery-only correction.

Before adding tables, the source-count snapshot was saved on the data PVC at
`/backups/maintenance/news-20261102/before.json`, SHA-256
`0dc5798b60a604fca509298ad16d63ef49e3fc3fb406d35479f19c0d45f777b2`.
The new lane uses separate tables; it does not update official source rows.

The first live discovery checked all three publishers and queued eight recent
pages. Two had no matching bill; six were national syndication with unapproved
canonical originals. The latter were verified again in a read-only worker canary,
backed up, and classified as `out_of_scope` with guarded updates. Their attempt
counts were kept. Backup: `syndication-before.json` in the same folder, SHA-256
`7ad5ba7b652b6235750a2bf6cb7c979bf4b4ce4660fdc2f34519803beff04ee9`.

A separate historical WyoFile article completed through the real durable
background queue and returned no qualifying reason. It is `no_reason_found`, not
an active queue failure. This is distinct from the four source-reviewed starter
reasons, which are actually saved and published. No automatic candidate was
published merely because the AI request or Kubernetes Job completed.
