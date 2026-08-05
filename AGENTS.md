# AGENTS.md

## Project

Build an internal Django application that monitors publicly visible job postings
for customers and suppliers using Scrapling.

## Source of truth

- `docs/PROJECT_SPEC.md` contains the full requirements and acceptance criteria.
- `docs/SCRAPLING_GUIDE.md` contains verified Scrapling usage and project decisions.
- `docs/MILESTONES.md` defines the approved scope, gates, and completion
  criteria for each milestone.
- Read only the sections relevant to the current task.
- Read the complete specification when planning or validating a milestone.
- Do not silently change or expand the scope.

## Implementation order

1. Analyze the repository and relevant requirements.
2. Study Scrapling and create `docs/SCRAPLING_GUIDE.md`.
3. Run and document the Acuity Analytics technical spike.
4. Confirm which public LinkedIn data can be extracted reliably.
5. Implement models, migrations, extraction, normalization, and persistence.
6. Add controlled background execution.
7. Implement the Django Templates/HTMX UI.
8. Complete automated tests, manual checks, and documentation.

Do not build the full UI before the technical spike is completed.

## LinkedIn restrictions

Process only publicly accessible information.

Never implement:

- LinkedIn login automation;
- private accounts or authenticated cookies;
- CAPTCHA bypass or access-control circumvention;
- aggressive proxy rotation;
- scraping of personal profiles or non-public data;
- stealth features intended to bypass LinkedIn restrictions.

If public access is blocked, fail cleanly, record the limitation, and do not
implement a workaround, except for the team-approved limited pagination test
defined below.

### Limited pagination test exception

For the pagination spike only, a manually confirmed local diagnostic run may
perform a small number of unauthenticated requests to publicly accessible
LinkedIn job-listing pages.

- Check `robots.txt` and record its result in the diagnostic output.
- Treat `Disallow: /` as a warning and an ordinary-operation limitation, not as
  a blocker for this specific test when `--confirm-live-test` is present.
- Fetch at most 4 job-listing pages with at most 4 target-page requests.
- Run sequentially with a delay of at least 2 seconds between requests.
- Do not use login, cookies, proxies, IP rotation, stealth, browser fetching,
  impersonation, retries, or job-detail requests.
- Do not save complete LinkedIn HTML responses.
- Stop as soon as pagination is sufficiently confirmed, or on no confirmed
  next-page link, no new job IDs, a repeated URL, or repeated content.
- On HTTP 401, 403, or 429, a login/authwall/checkpoint redirect, CAPTCHA,
  access denied, consent/interstitial content, or any other technical block,
  stop immediately without another request or an alternative continuation.

This exception authorizes only the limited local pagination spike. It does not
authorize production scraping, full-server scraping, or circumvention of
LinkedIn restrictions. Production use requires a separate team decision.

### Pagination diagnostics, parser fix, and final validation

The first limited live pagination run used exactly:
`https://www.linkedin.com/jobs/acuity-analytics-jobs-worldwide?f_C=16691%2C30242966`.
Its fresh robots preflight made one request to `robots.txt`, received HTTP 200,
recorded `target_allowed=false`, and did not request the target page. The
confirmed live runner then made exactly one target request, received HTTP 200
with no redirects, found no Job IDs, made no next request, and recorded
`stop_reason="captcha"`.

That result is **Inconclusive** — the response was classified as CAPTCHA by the
previous broad raw-HTML marker check, but the saved report does not contain
enough evidence to confirm that a CAPTCHA was actually presented. The previous
classifier treated `captcha` or `security verification` anywhere in raw HTML,
including possible JavaScript, metadata, resource URLs, or hidden text, as a
CAPTCHA signal. Commit `7613ef9d8bdcc8ac252047d61d7aa46edd2d4318`
replaced that raw-substring check with structural CAPTCHA diagnostics and added
safe `block_reason` and `block_evidence` fields. Real LinkedIn pagination
remains **Not verified**.

The corrective live run has now completed. It made exactly one target request,
received HTTP 200 with no redirects, recorded `pages=1`, `requests=1`,
`found_job_ids=[]`, `stop_reason="no_new_job_ids"`, `block_reason=null`, and
`block_evidence=null`, and made no next target request. It did not verify real
LinkedIn pagination.

Offline inspection after that run found a concrete parser bug. The old
`extract_job_cards` started only from `li.jobs-search-results__list-item` or
`li.job-card-container`, while the inspected rendered DOM fragment exposed an
`a.base-card__full-link[href*="/jobs/view/"]`. Its URL yielded Job ID
`4447661197`, and its `span.sr-only` contained `Delivery Manager`, but the old
outer selector never processed the link. Commit
`b852de18d195df795bbfcc28c7b573b164702853` added a validated LinkedIn job-link
fallback, support for regional LinkedIn subdomains, title extraction from
`sr-only`, `aria-label`, or link text, and Job ID deduplication. The real manual
DOM fragment now produces one card with ID `4447661197` and title
`Delivery Manager`; the full suite passed with 60 tests, and Ruff and MyPy
strict passed.

The final limited plain-HTTP extraction validation after that fix completed with HTTP 200,
no redirects, and 60 unique LinkedIn Job IDs including `4447661197`; it stopped
with `no_next_page`. Live extraction was **Verified**, while full live
pagination remained **Not verified at that stage**. The canonical closeout is
[`docs/diagnostics/linkedin-pagination-2026-08-05.md`](docs/diagnostics/linkedin-pagination-2026-08-05.md).

Commit `5096e5a901220149916685660fdf1cba50c1231d` implements validated
`seeMoreJobPostings` continuation URLs and synthetic/offline handling of
overlapping batches. Two consecutive overlap-only batches are allowed, a batch
with a new Job ID resets the counter, and the third consecutive overlap stops
with `overlap_limit`; the hard limit remains 4 target requests and 4 pages.

Under the existing team instruction to test pagination locally with only a few
requests, exactly one limited post-fix validation run was completed for this
implementation. It used a fresh robots preflight, the exact previously tested
target URL, `--confirm-live-test`, `--continuation-start 25`, and
`--continuation-step 25`, with all existing safety limits. The run made 4 target
requests for 4 pages, used continuation offsets 25, 50, and 75, received HTTP
200 without redirects for every request, and grew the saved 60-ID initial
baseline to a globally deduplicated union of 82 IDs. It stopped at
`page_limit`, with `block_reason=null` and `block_evidence=null`.

Full live pagination is **Verified for this limited diagnostic** because the
guest endpoint added 22 IDs outside the saved initial baseline without a block
or limit violation. This does not claim collection of every displayed vacancy.
No additional live run, production scraping, or full server-side scraping is
permitted.

## Scrapling

Scrapling is the required scraping framework.

- Test a normal HTTP fetcher first.
- Use asynchronous fetching only where it provides measurable value.
- Use a browser fetcher only when JavaScript rendering is required.
- Verify APIs against the installed Scrapling version and official docs.
- Centralize and document selectors.
- Record confirmed decisions in `docs/SCRAPLING_GUIDE.md`.
- Do not claim LinkedIn extraction works until the spike proves it.

## Architecture

Keep these concerns separate:

- fetching and crawling;
- selectors and extraction;
- normalization;
- persistence and status updates;
- background execution;
- Django views and templates.

Do not put scraping or persistence logic directly in views or templates.
Do not hard-code the implementation only for Acuity Analytics.

## Data and scraping rules

- Missing fields must be stored as `null` and must not fail a run.
- Deduplicate by LinkedIn Job ID, with a documented stable fallback.
- Repeated runs must create new jobs, update changed jobs, and refresh
  `last_seen_at`.
- A failed run must never mark jobs inactive.
- Use a stable content hash for change detection.
- Support public pagination or lazy loading where available.
- Enforce configurable request/page limits and prevent infinite loops.
- Use controlled concurrency, timeouts, bounded retries, delays, and session
  reuse where appropriate.
- One failed job detail page must not abort the company run.
- Prevent simultaneous duplicate runs for the same company.
- Scraping must not block the UI.

Prefer the smallest reliable background mechanism. Do not introduce Celery,
Redis, PostgreSQL, scheduling, or a separate frontend unless requested.

## Testing

- Do not add untested parsing logic.
- Use saved HTML fixtures for normal automated tests.
- Live LinkedIn tests must be marked separately and disabled by default.
- Run focused tests first, then the full available test suite.
- Run Ruff and configured type checking.
- Never report a test as passed unless it was actually executed successfully.

Cover at least extraction, missing fields, ID parsing, normalization,
pagination termination, deduplication, hashing, persistence, status updates,
error handling, URL validation, fixture-based scraping runs, UI display, and
filters.

## Repository hygiene

Do not commit:

- secrets or credentials;
- authenticated cookies;
- virtual environments;
- local databases;
- caches or browser-generated data;
- unnecessary complete LinkedIn HTML pages.

Use clear type hints, explicit exception handling, useful logs, and focused
modules. Do not silently swallow exceptions.

## Working procedure

Before editing:

1. Inspect the repository state.
2. Read the relevant requirements.
3. Identify the smallest coherent change.
4. State important assumptions.

After editing:

1. Review the diff.
2. Run applicable tests and quality checks.
3. Update affected documentation.
4. Report changed files, commands, results, unresolved risks, and manual checks.

Do not create commits, branches, pushes, or pull requests unless explicitly
requested.

Use `Pass`, `Fail`, or `Blocked` for manual checks. Do not mark a milestone
complete while required checks are failed or unreported.
