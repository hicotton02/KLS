# Wyoming Recording Repair - September 12, 2026

## Findings

The 161 `source_unavailable` entries were not 161 missing recordings:

- 126 audio decoding failures, including 117 bad 2018 `AudioMenu` paths.
- 16 rejected transcripts and one response with no timestamped speech.
- 16 YouTube videos reported unavailable and one private video.
- One malformed recording URL.

All 23 corrected 2018 links without a completed counterpart returned real audio
in bounded live range requests. Further checks verified the 2008 March 3 House
filename and 2015 January 22 Senate filename corrections. Unverified filename
guesses were not applied.

## Applied Repair

The backed-up transaction changed 144 rows: 99 duplicates now point to their
main recording, 44 unique recordings entered a controlled recovery pass, and
one malformed link became `source_invalid`. All 2,620 existing transcripts and
14,191 published/curated reasons were preserved. A second dry run proposed zero
changes. Re-discovery of all 236 source entries for 2018 created no new rows.

Original records remain in the database. The before-state backup is stored at
`/home/theskaz/wyoming-media-before-20260912.json` on the control plane and in the
operator workstation's temporary directory. SHA-256:
`903feee6e98347f11703215004502eaa11359353376ae91f31934648293f4b33`.

## Release And Verification

Release: `20260912-mediarepair2`, digest
`sha256:38bdd34df5b6d9a8b6120b7bece0022156e490aff7d6a3395565d7ae890b6e28`.
The release reuses the weekly3 runtime and adds the application changes without
changing dependencies. The web deployment and four recurring jobs use the new
digest. The public frontend was not redeployed.

- 258 automated tests passed.
- The production repair1 rollout passed 114 public probes across all 52 areas.
- Both hostnames passed readiness and health checks.
- Eric Barlow search still returned exactly one person.
- The first three recovered recordings (1315, 1325, 1326) produced transcripts.
- Four scheduled workers process the remaining recovery queue; completion of
  the release does not imply completion of this historical transcription pass.
- Recording 480 encountered a YouTube 403 and remains retryable after cooldown.
- Quality checks are unchanged. Rejected speech is held, not published.

Monitor `kls_legislative_media_backlog` and `kls_legislative_media_issues` together.
A zero active backlog must not be reported as full recording coverage.
