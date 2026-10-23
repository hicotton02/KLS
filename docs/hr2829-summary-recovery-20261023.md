# HR2829 Summary Recovery

Completed October 23, 2026, at 5:09 AM Mountain.

## Cause And Repair

HR2829, the SERVICE Act in Congress 119, was the last of the 110 summaries held
for review in the October 17 repair. Its draft was complete at 577 tokens. The
fact-check response, however, expanded into lengthy commentary and hit its
2,000-token limit without finishing its JSON. The incomplete response was correctly
rejected; raising the draft limit would not address that failure.

The fact-check now uses a bounded JSON schema and asks for corrected facts and
brief source-based notes, without commentary about the checking process. Unsupported
sections may still be empty. The existing evidence rules, fact-check stage,
content-quality checks and rejection of cut-off responses remain unchanged.
Draft/fact-check limits remain 1,400/2,000 tokens. The actual repaired fact-check
completed normally at 728 tokens.

## Controlled Recovery

- Verified `kubernetes-admin@kubernetes`, API `https://192.168.104.50:6443`, and
  namespace `keeping-law-simple` before changes. Git remote is
  `https://github.com/hicotton02/KLS.git`; unrelated dirty profile work in the older
  checkout was not touched.
- Reproduced the original failure through its durable shared-queue request,
  without changing the held record or dispatching a duplicate completed request.
- Tested the repair privately against the real bill, first with a source overlay,
  then from the actual immutable image. Both had read-only database access and no
  Service or ingress. The image source matched the tested file and ran as UID 10001.
- Updated only the federal summary CronJob image. Existing workers drained before
  the review was approved. Public frontend/API deployments were not restarted;
  Wyoming/source CronJob images, schedules, resources and routing stayed unchanged.
- Approved only bill ID 6608325 using `review_held_summaries.review_one`, after
  backing up its complete bill/queue record to durable NFS. Attempts stayed at
  three; no retry limit or source hold was reset.
- A bounded normal queue worker claimed and saved the validated result. The
  queue row is `complete`, with no owner, lease, cooldown or error. Its source
  version and full source text are unchanged. The prior interpretation was
  preserved in `bill_summary_history` at `2026-10-23T11:09:06+00:00`.

Backup: PVC `keeping-law-simple-data`, mounted at `/backups`, path
`maintenance/hr2829-20261023/review-before.jsonl`. Readback verified the NFS mount,
the original held record, size 38,678 bytes, and SHA-256:

```text
df7a43c89ea99f801008998443c36a3d5550c104a2d212fcc68d8435538e53a7
```

## Verification

- New regression failed before the fix. Focused tests: 47 passed.
- Full backend: 414 passed; two optional PostgreSQL tests skipped locally.
  Database logic was not changed; the private real-data canary and saved-history
  readback passed. Frontend production build and all 31 frontend tests passed.
- Public health/readiness, HR2829 API and actual bill page returned 200. The API
  reports `summary_ready: true` and `fact_check_status: validated`; rendered HTML
  contains the finished summary instead of the unfinished-explanation notice.
- The next normal four-worker federal CronJob completed with zero failures.
- Federal queue: 19,693 complete, zero review/quality holds or active processing
  backlog. Wyoming: 8,721 complete, zero held summaries. All 110 original review
  holds are now recovered.
- The 82 separate federal source holds remain. They are not output failures and
  were not forced into generation using a title alone. This repair is not an
  all-jurisdiction source-coverage audit.
- Zero expired summary leases or blocked database queries. Both public
  deployments remain 2/2 ready, public pods have zero restarts, and bounded fresh
  API/frontend error checks are clean.

## Release And Evidence

Federal summary image:

```text
registry.skazproconsulting.com/keeping-law-simple/web:20261023-hr2829-bounded1@sha256:4aa8372568f5e2810763738d3e4269c70a1b0996f39446717811dbb9b730747c
```

Tested `app/ollama.py` SHA-256:
`f8a84dc4691e7336fb3bf0cc7682efe57475a5933b8910ab71d5c201fd8ab40d`.

Local evidence: `tmp/hr2829-20261023-*.json`. Review and drain Jobs retain eight
days of history. Temporary diagnostic, image-canary and backup-verification pods
and the completed build Job are removed after log/readback capture. The durable
backup and published summary stay intact. Do not replay the review approval or
restore an old held queue row over the completed result.
