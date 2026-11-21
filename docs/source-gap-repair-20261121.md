# Official Source Gap Repairs - November 21, 2026

## Sourcing Rule

The owner confirmed official sources only. No LegiScan integration, key,
third-party feed, paid service, or Sites privacy change was added.

## Federal Repair

Congress's bill-text feed and GovInfo's normal introduced-bill files had no
usable text for 156 held bills. Eleven other holds are reserved bill numbers,
not bills with released text. Five agreed Senate resolutions also had no
GovInfo agreed-version file. A missing file can redirect to GovInfo's error
page with HTTP 200; that page is not bill text.

A deeper check found an exception: S5767's latest official action cites the
full text in the Congressional Record. The full bill is printed there, even
though its normal bill-text download is missing. The new fallback:

- Runs only when the normal text-version list is empty.
- Requires a text citation in the latest official action, from the right
  Congress, date, and chamber. It does not search random pages or use titles
  as a substitute for source text.
- Checks the Record's date, chamber, cited page, exact bill heading, enacting
  clause, first section, and ending. Mixed bills or incomplete text stay held.
- Excludes the member's introductory remarks and rejects speaker remarks after
  the printed bill. It never summarizes those as though they were bill text.
- Fetches only GovInfo's official public URL, with a 4 MB download bound and no
  Congress API key attached. Access restrictions are not bypassed.
- Uses the same adapter path for catch-up and daily refreshes. Later scans
  therefore keep the same verified source rather than replacing it with a
  blank field. A normal published bill version takes priority when it appears.

Two private canaries, using the separate live worker bases, produced identical
115,191-character bill text and the same source hash. They used read-only
production database sessions. A backed-up, lease-protected recovery then saved
the source, left the existing bill identity and summary limits alone, and sent
the bill into the normal shared AI queue.

Official source: [S5767 in the Congressional Record](https://www.govinfo.gov/content/pkg/CREC-2026-11-18/html/CREC-2026-11-18-pt1-PgS5761.htm).

The previous pending summary, HR11037, is also complete and visible through the
public API. Source coverage remains 20,203 official identities and stored bills,
with no source inventory deficit. Missing text is tracked separately.

At 8:24 AM Mountain, S5767's summary passed validation and was saved on its first
attempt, with a matching history entry. Its public API and page have the summary
and official source link. The summary states the limits of its long-text excerpt;
the complete official text remains linked. Federal now has 20,037 validated
summaries and 20,665 history entries. There are no pending, processing, waiting,
retry, quality, or review holds. The 166 remaining source holds are 155 bills
without usable text in the checked feeds and 11 reserved numbers.

The exact remaining lists are saved with this report:
[Federal source holds](federal-source-holds-20261121.csv) and
[Wyoming recording holds](wyoming-media-holds-20261121.csv).

Two older holds were checked more closely. HR10768 has an official sponsor
press release linking to bill text, but that page returned HTTP 403; it was not
bypassed or used as a substitute for the bill. HRES1378's cited Record page is
the sponsor's speech, not resolution text. Both remain held.

## Wyoming Recovery

Short test clips from held recordings passed with the current transcription
service, so five full recordings received one bounded recovery attempt each:
1213, 1257, 2705, 2744, and 933. Only two recordings ran at once, with two audio
chunks per recording in flight. All five still contained sections that failed
the existing speech checks, including after one-minute retries. A separate
diagnostic of 2702 also failed at the opening audio, although later speech was
clear. A good sample is not proof that a whole recording is safe to publish.

None of these test transcripts was published. Existing transcripts, reasons,
and scan status were left unchanged; routine discovery timestamps continued
to update. The five Jobs
completed their tests; they did not complete the missing transcripts. No GPU
fault or shared-queue congestion was found.

A tested maintenance tool now separates generation from publication. It checks
the official recording's year, session, date, chamber, time of day, and URL.
It requires an unused durable backup before transcription, and saves candidates
privately. Publication rejects invalid timestamps or changed source rows and
will not overwrite existing evidence. Repeating the same candidate generation
is blocked by the exclusive backup file. No automatic retry loop was added.

Recovery files are on the existing shared maintenance storage:

```text
/archive/held-media-recovery-20261121/<media-id>-before.json
/archive/held-media-recovery-20261121/<media-id>-candidate.json
```

Do not repeat these five attempts without a better official source or a tested
transcription repair. The source/evidence checks must not be lowered to clear
the dashboard.

The 15 speech-quality holds remain. Three malformed/missing archive links still
return HTML, including checks of the official www alias and the plausible
corrected 2013 Senate filename. Seventeen previously confirmed private or
unavailable videos remain terminal source gaps, not active processing failures.
The two incomplete 2006 bill scans still need usable official audio. Existing
published reasons and all 8,721 validated Wyoming summaries remain intact.

## West Virginia

Both official hostnames time out from production and from the workstation.
Independent public fetching also timed out. DNS points to the same official
addresses. A final bounded production check still failed at connection setup.
This is an outside source-access blocker, not a database or GPU problem.
No network settings, login restrictions, or credentials were changed. Existing
bills remain available; the last successful scan is November 19. Current source
coverage must not be marked complete until the official site responds.

## Validation And Release

- 511 backend tests passed. Three optional PostgreSQL checks also passed using
  rolled-back temporary tables, without changing public rows.
- Focused tests cover Record identity, date/chamber mismatch, partial/mixed text,
  API-key isolation, unchanged refresh behavior, and access restrictions. Media
  tests cover failed backup, preserved holds, changed rows, invalid timestamps,
  and idempotent publication.
- All 120 public checks passed across all 52 areas, including Wyoming, Eric
  Barlow's single result, and 2020 SF0139. Public API/frontend logs had no errors.
- Only the two source CronJob image fields changed. Readback verified their
  schedules, commands, limits, and all other settings stayed unchanged. Public
  API, frontend, summary-worker, and regular transcription images stayed pinned.
  The worker overlays preserve the earlier state-feed repairs.

```text
Federal catch-up:
registry.skazproconsulting.com/keeping-law-simple/web:20261121-record3-federal@sha256:c4803a0e0337f6e9e62bf8fedd27f44448dbff97ae17817e3797fc132fe214ec

Daily source scan:
registry.skazproconsulting.com/keeping-law-simple/web:20261121-record3-sync@sha256:cc6d6be57579acf8eeb15a6ab0def3850a5c2fbcfa4340c15b8d1b73036072c1

Private held-media recovery tool:
registry.skazproconsulting.com/keeping-law-simple/web:20261121-held-media1@sha256:e13adf81aa7b255acf4710555d06a7b6f6b55a5220a2d048b6352e75c80fb47b
```

The weekly schedule is unchanged. This is a partial recovery, not a full
all-clear: West Virginia access, unreleased federal text, and unusable Wyoming
recordings remain separate outside-source or quality blockers.
