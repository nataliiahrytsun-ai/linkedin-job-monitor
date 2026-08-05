# LinkedIn Pagination Diagnostic — 2026-08-05

## Status

- **Live extraction: Verified.** After the parser fix, the final limited
  plain-HTTP validation received HTTP 200 without redirects and extracted 60
  unique LinkedIn Job IDs. The manually checked Job ID `4447661197` was present.
- **Continuation endpoint: Verified.** Browser Network inspection first
  identified, and the post-fix runner then requested, the public GET endpoint
  `/jobs-guest/jobs/api/seeMoreJobPostings/acuity-analytics-jobs-worldwide`.
- **Full live pagination: Verified for the limited diagnostic.** The single
  post-fix validation used three continuation offsets and added unique Job IDs
  beyond the saved initial 60-ID baseline without exceeding its limits.

The earlier extraction-validation runner stopped after one page with
`stop_reason="no_next_page"`; the later post-fix continuation validation below
supersedes that result for pagination status.

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

## Post-fix validation result

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

The required fresh preflight made exactly one request to
`https://www.linkedin.com/robots.txt`, received HTTP 200 without redirects,
recorded `target_allowed=false`, and did not request the target. That result was
recorded as a diagnostic warning for the explicitly confirmed limited run.

Exactly one post-fix validation run then used the exact target URL,
`--confirm-live-test`, `--continuation-start 25`, and `--continuation-step 25`.
Its actual result was:

- requested pages: 4;
- target requests: 4;
- HTTP statuses: 200, 200, 200, 200;
- redirects: none;
- continuation offsets used: 25, 50, 75;
- saved initial baseline: 60 unique Job IDs;
- final globally deduplicated union: 82 unique Job IDs;
- IDs outside that initial baseline: 22;
- `stop_reason="page_limit"`;
- `block_reason=null`;
- `block_evidence=null`.

All 60 IDs in the saved initial baseline were present in the final union. The
22-ID delta is therefore the continuation contribution relative to that
baseline. The run remained sequential, waited at least 2 seconds between target
requests, and did not exceed 4 target requests or 4 pages. No complete live HTML
was saved. No second post-fix run is permitted or was performed.

## Conclusion

Live plain-HTTP extraction is **Verified**. The continuation endpoint is
**Verified**. Full live pagination is **Verified for this limited diagnostic**:
the guest endpoint was actually used, later batches contributed unique Job IDs,
the limits were respected, and no technical block was detected. This does not
claim that all displayed vacancies were collected or authorize production use.

## Next engineering step

No further live validation is authorized. Any future engineering work must use
the saved diagnostic result and synthetic/offline tests unless the team makes a
separate production decision.
