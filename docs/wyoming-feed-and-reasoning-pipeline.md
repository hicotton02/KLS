# Wyoming freshness and voting-reason pipeline

Status: production worker design. Published-statement discovery remains beta.

## What the citizen sees

- Wyoming bill and vote records stay current without waiting for summaries or transcripts.
- A lawmaker's vote can show a short **Why they said they voted this way** section.
- Each reason is labeled **Official statement** or **Published quote**.
- Every reason includes a direct **Read source** link to the exact recording, article, or official release.
- When no qualifying statement is available, show: **Couldn't find a published reason.**
- Never show inference engine, backend, prompt, or model details in the public site or public API.

## Bottlenecks observed before the August 4, 2026 rollout

- Two eight-pod source jobs overlap and repeat the same state work.
- Wyoming waits behind West Virginia and Wisconsin in a static shard.
- Official source ingestion is coupled to plain-language enrichment.
- A backend identifier change makes old summaries look stale even when the source and prompt are unchanged.
- Recording discovery, transcription, reason extraction, and bill-status refresh run serially.
- Failed recording and extraction rows are not automatically retried.
- Bill explanation status refresh performs one count query per bill.

Those issues are addressed by the production worker layout below. Transcription uses the existing shared service; KLS workers stay CPU-only and use bounded claims so they do not reserve accelerator capacity or duplicate work.

## Transcription benchmark result

The August 4, 2026 isolated benchmark used ten Wyoming recordings totaling 5.53 hours. It read production caption baselines but did not write to production.

The original whole-file path failed quality because long sessions produced repeated text and missed short bill references. The corrected path uses two-minute primary chunks, voice-activity filtering, segment-level repetition and compression checks, speech-density limits, and one-minute retries for rejected chunks. It also retries transient recording-download failures three times with bounded backoff.

Final evidence set:

- Service reliability: 10 of 10 recordings completed.
- Public-quality acceptance: 10 of 10 recordings passed.
- Bill-reference recall: 100 percent across all seven samples with caption-detected bills.
- Transcript word count: 88.1 to 104.5 percent of the published-caption baseline.
- Average transcript overlap recall: 70.1 percent.
- Average processing speed: 17.10 times real time under shared load.
- Slowest processing speed: 9.70 times real time.
- Bill hotword prompting stayed disabled because it produced repetitive false text in an isolated comparison.

The final evidence combines the complete ten-recording run with one targeted rerun after adding the sparse-heavy-rejection guard. That guard changed only the targeted recording: the other nine did not meet its combined rejection-and-density condition.

The validated path is wired into the production transcription worker. A completed transcription request is still not a quality pass; the same repetition, density, and bill-reference checks remain required before a transcript is accepted.

## Production worker layout

The recording pipeline is split into three bounded CronJobs:

1. `keeping-law-simple-wyoming-media-discovery` catalogs recordings hourly.
2. `keeping-law-simple-wyoming-transcriptions` runs two workers, each claiming up to four recordings.
3. `keeping-law-simple-wyoming-reasoning` runs two workers, each claiming up to two completed transcripts.

Transcription and reason extraction claim work with conditional database
updates before processing. A second worker cannot claim the same recording.
Claims abandoned by a failed pod become eligible again after the matching
Kubernetes job deadline. This gives the historical queue more throughput
without assigning GPUs directly to KLS pods or starving interactive services.
Items that fail transcription or reason extraction wait six hours before they
can be claimed again, preventing a bad recording from spinning in a tight loop.
Reason extraction uses the shared service's background queue and a timeout that
matches the queue window, so historical work cannot jump ahead of interactive
requests or abandon a request while it is still waiting normally.

## Pipeline lanes

### 1. Official-source lane

Run Wyoming independently every 15 minutes during the session and every hour outside the session.

1. Claim the state with an atomic database lease.
2. Fetch bill, action, sponsor, vote, and roster changes.
3. Store only changed source records and update `last_source_scan_at`.
4. Emit durable enrichment tasks for changed bills.
5. Release the lease even when one source endpoint fails.

This lane must not wait for summaries, transcripts, article searches, or reason extraction.

### 2. Plain-language lane

- Consume changed-bill tasks independently.
- Version output by source-content hash, prompt version, and schema version.
- Do not invalidate a valid summary merely because the serving backend changed.
- Retry 429 and transient errors with bounded exponential backoff and jitter.
- Checkpoint each bill so a worker restart loses at most one item.

### 3. Official-recording lane

The production worker is split into independently retryable stages:

1. Discover new Wyoming recordings once per year/session scan.
2. Prefer published captions; queue only missing captions for transcription.
3. Split missing-caption audio into independently leased two-minute chunks.
4. Enable voice-activity filtering and reject repetitive, sparse-after-heavy-rejection, or impossible-density output.
5. Retry rejected chunks as one-minute pieces and retain the original timestamps.
6. Reassemble accepted chunks with corrected timestamps.
7. Extract bill sections and personal voting explanations per bill.
8. Refresh bill-level coverage with set-based SQL.

Caption downloads use bounded retries. A temporary caption error, including a
publisher `429`, falls through to the configured transcription service instead
of marking the recording permanently failed.

The current statuses are `pending`, `transcribing`, `scanning`, `available`, `complete`, and `failed`. Failed transcription and reasoning rows become eligible again after a six-hour cooldown. Stale `transcribing` claims expire after three hours, and stale `scanning` claims expire after six hours. Timeouts and malformed extraction output therefore return to the queue without creating a tight retry loop.

Long transcription claims expire after three hours. Reason-extraction claims
expire after six hours. Fresh and interactive workloads keep priority; KLS
historical work uses bounded parallelism and waits without duplicating itself.

### 4. Published-statement lane

Start with an allowlist of Wyoming publishers and official lawmaker releases. Initial candidates include Cowboy State Daily, WyoFile, Wyoming Public Media, and official legislative or lawmaker sites.

Discovery can use publisher RSS feeds, sitemaps, and site search. Do not bypass paywalls, access controls, or robots rules.

A source qualifies only when it contains all of the following:

- A named Wyoming lawmaker.
- A specific bill or vote that resolves to an official KLS bill record.
- A direct quote or an unmistakably attributed paraphrase explaining that lawmaker's own vote.
- A publication date and exact canonical article URL.

Do not infer motive from a headline, party position, advocacy group, reporter framing, or how other lawmakers voted.

Evidence classes:

- `official_floor_statement`: timestamped official video or transcript.
- `official_press_release`: statement published by the lawmaker or legislature.
- `reported_direct_quote`: short direct quote in reputable reporting.
- `reported_paraphrase`: reporter's explicit attribution of the lawmaker's reason.

Only official statements and high-confidence direct quotes should publish automatically in beta. Reported paraphrases and uncertain bill matches go to review.

Store publisher, article title, publication date, canonical URL, attribution class, a short evidence excerpt, neutral reason summary, bill match, lawmaker match, vote match, confidence, and review status. Keep displayed excerpts short; link to the publisher for context.

Examples showing why this lane is useful include Cowboy State Daily reporting that ties a named lawmaker, a specific bill, a recorded vote, and the lawmaker's stated reasoning. The article remains the evidence; KLS only summarizes the attributed reason and links back to it.

Research examples for parser and review tests:

- [Wyoming Lawmaker Accused Of Voting Against God Avoids Party Censure](https://cowboystatedaily.com/2023/10/18/wyoming-lawmaker-accused-of-voting-against-god-avoids-party-censure/)
- [Wyoming Lawmaker Says He Was Threatened To Be Removed From Committee Over No Vote](https://cowboystatedaily.com/2023/02/18/wyoming-lawmaker-says-he-was-threatened-to-be-removed-from-committee-over-no-vote/)

These are test inputs, not automatically accepted facts. The pipeline still has to match the named lawmaker, bill, official vote, attributed reason, date, and canonical URL before publication.

## Simple public presentation

Use one compact block on a bill vote or lawmaker record:

> **Why they said they voted this way**
> They said the bill was too vague and could create unintended liability.
> **Published quote** | Cowboy State Daily | Feb. 18, 2023 | **Read source**

When sources disagree, show each qualified statement separately and do not choose a motive for the citizen.

## Data and job safety

- Use unique keys on source URL plus article revision, and on recording plus time range plus lawmaker plus bill.
- Keep the original excerpt and source metadata immutable; revisions create a new evidence version.
- Record fetch time, HTTP status, content hash, parser version, and match decision.
- Apply per-domain rate limits, conditional requests, 429 `Retry-After`, and a dead-letter queue.
- Keep unpublished candidates out of public endpoints.
- Never write benchmark output to production tables.
- Reject transcripts whose segment compression, repeated text, speech density, or bill-reference checks fail.

## Performance targets

- Wyoming official-source freshness: 15 minutes during session, 60 minutes otherwise.
- Source scan duration: under 5 minutes at the 95th percentile.
- New published statement discovery: under 2 hours.
- Transcription throughput: faster than 10 times real time on average, with the slowest recording still faster than real time.
- Bill-reference recall versus published captions: at least 90 percent on samples that contain detected bills.
- Transcript word count: between 40 and 180 percent of the published-caption baseline unless reviewed.

## Rollout order

1. Add state leases and a Wyoming-only source schedule.
2. Decouple source ingest from plain-language enrichment.
3. Fix chamber identity and unresolved legislator aliases before rebuilding Wyoming votes.
4. Wire the validated two-minute transcription path into an independently leased Wyoming worker.
5. Canary new missing-caption recordings before starting the historical backfill.
6. Split and retry the remaining recording stages.
7. Add the Wyoming publisher allowlist and review queue.
8. Release the published-statement UI to a private beta.
9. Validate freshness, source links, identity matches, and public wording before production.
