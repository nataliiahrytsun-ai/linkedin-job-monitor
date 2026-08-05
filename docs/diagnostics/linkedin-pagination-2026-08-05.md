# LinkedIn Pagination Diagnostic — 2026-08-05

## Status

- **Live extraction: Verified.** After the parser fix, the final limited
  plain-HTTP validation received HTTP 200 without redirects and extracted 60
  unique LinkedIn Job IDs. The manually checked Job ID `4447661197` was present.
- **Continuation endpoint: Observed.** Browser Network inspection manually
  identified the public GET endpoint
  `/jobs-guest/jobs/api/seeMoreJobPostings/acuity-analytics-jobs-worldwide`.
- **Full live pagination: Not verified.** The runner now implements the
  continuation endpoint and overlapping-batch handling, but that behavior has
  only synthetic/offline evidence so far.

The final runner stopped after one page with `stop_reason="no_next_page"`.

## Confirmed pagination evidence

- The rendered browser DOM contained
  `button.infinite-scroller__show-more-button`.
- The observed continuation request uses the `start` parameter; it does not
  continue by incrementing `pageNum`.
- The Browser Network log contained observed requests with `start=25` and
  `start=175`.
- The locally saved `start=25` response contained 10 job cards.
- All 10 Job IDs from that batch were already present in the initial 60 IDs:
  `new_ids=[]`.
- Given that batch after the initial 60, the current runner would stop with
  `no_new_job_ids`.
- The observed `start=175` response was an empty HTML fragment.

These observations do not establish a fixed `start` step, do not establish the
values between 25 and 175, and do not show that all 131 displayed vacancies were
automatically collected.

## Post-fix validation preparation

Commit `5096e5a901220149916685660fdf1cba50c1231d` implements a validated
`seeMoreJobPostings` continuation URL. It retains only the confirmed `f_C`
search parameter and uses explicit `continuation_start` and
`continuation_step` values rather than inferring an offset sequence.

Synthetic/offline tests confirm that Job IDs are deduplicated globally, one or
two consecutive overlap-only continuation batches are allowed, a batch with a
new ID resets the overlap counter, and a third consecutive overlap-only batch
stops with `overlap_limit`. Empty batches, repeated URL/content, technical
blocks, and the hard limits also stop the runner. No network requests were made
by these tests.

Under the existing team instruction to test pagination locally with only a few
requests, exactly one limited post-fix validation run is permitted. It requires
a fresh robots preflight, the exact target URL previously used,
`--confirm-live-test`, `--continuation-start 25`, and `--continuation-step 25`.
The maximum remains 4 target requests and 4 pages, sequentially with at least a
2-second delay and all existing no-login/no-cookie/no-proxy/no-stealth/no-retry
restrictions. No additional live run or production use is permitted.

## Conclusion

Live plain-HTTP extraction is **Verified**. The continuation endpoint is
**Observed**. Full live pagination remains **Not verified** because the runner
has not yet produced reliable live evidence that the implemented continuation
handling reaches a later batch with a new unique Job ID.

## Next engineering step

Run the single documented limited post-fix validation and record only its
actual diagnostic JSON result. Do not infer offsets or perform another run.
