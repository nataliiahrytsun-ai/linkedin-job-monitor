# Acuity Analytics LinkedIn Technical Spike Report

## Decision

**Classification: `Not feasible through compliant public access`.**

**Milestone status: `Completed — Not feasible through compliant public access`.**

On 2026-07-28, a normal Scrapling HTTP session fetched LinkedIn's public
`robots.txt`. It returned HTTP 200 with no redirect and declared
`User-agent: *` / `Disallow: /`. The tested Acuity Analytics jobs URL is
therefore not fetchable by this project's ordinary crawler user agent. The
runner stopped before requesting the target. No login, cookie, proxy, browser,
stealth, CAPTCHA handling, or access-control workaround was used.

This decision means feasibility was disproved under the project's required
robots/terms boundary; it does **not** claim that the target URL is technically
unreachable in a browser.

## Experiment metadata

| Item | Observed value |
|---|---|
| Date | 2026-07-28 |
| Host environment | Windows, PowerShell, Europe/Vienna |
| Python | 3.14.0 |
| Scrapling | 0.4.8 |
| Target | `https://www.linkedin.com/jobs/acuity-analytics-jobs-worldwide?f_C=16691%2C30242966&trk=job-results_see-all-jobs-link&currentJobId=4434981246&position=39&pageNum=0` |
| Authentication | None |
| Cookies | None |
| Proxy | None |
| Tested fetcher | `FetcherSession` (HTTP), robots preflight only |
| Target requested | No |
| Async fetcher | Not tested |
| Dynamic browser fetcher | Not tested; policy gate stopped first |
| Stealth fetcher | Deliberately excluded |

The intended Python 3.12 environment was unavailable: the existing `.venv`
references a removed Python 3.12.6 executable. Python 3.13 was listed by the
launcher but could not start (`Access denied`). An ignored Python 3.14 spike
environment was used. This is outside Scrapling 0.4.8's declared 3.10–3.13
support even though installation, HTTP preflight, parsing, and tests worked.
Python 3.13 availability was checked again when the milestone result was
accepted; it still failed with `Access denied`, so the environment was not
recreated.

## Commands executed

Relevant commands (approval-mediated network commands are shown conceptually):

```powershell
py -0p
py -3.12 --version
python --version
py -3.14 -m venv .spike-venv
.\.spike-venv\Scripts\python.exe -m pip install "scrapling[fetchers]==0.4.8" pytest==8.4.1 ruff==0.12.5 mypy==1.17.0
.\.spike-venv\Scripts\python.exe -m spikes.linkedin_spike
.\.spike-venv\Scripts\python.exe -m pytest tests\test_extraction.py -q
.\.spike-venv\Scripts\ruff.exe check spikes tests
.\.spike-venv\Scripts\mypy.exe spikes tests
```

The first install process outlived its terminal yield and locked a package file;
it was stopped, then the same pinned install completed successfully. An initial
API probe incorrectly used `Fetcher.get(..., retries=0)`. In installed 0.4.8,
zero means zero total attempts and produced `RuntimeError: No active session
available`; the documented project setting now uses one total attempt.

## Live configuration and result

```text
timeout_seconds: 20
request_delay_seconds: 2
max_pages: 2
max_requests: 3
concurrency: 1
max_attempts_per_request: 1
follow_redirects: safe
stealthy_headers: false
impersonate: none
user_agent: linkedin-job-monitor-m1-spike
```

Final executed preflight result:

| Metric | Result |
|---|---:|
| `robots.txt` status | 200 |
| Redirects | 0 |
| Requests | 1 |
| Request time | 0.367 s |
| Total runner duration | 0.391 s |
| Target allowed by robots | No |
| Target request count | 0 |
| Timeouts | 0 |
| Incomplete responses | 0 observed for robots |
| HTTP blocks | None for robots |
| Policy block | Yes, before target request |

An earlier direct robots probe also returned 200 in 0.318 s with no redirect.
The final runner measurement above is the canonical result.

## Requested spike questions

| Question | Evidence-backed result |
|---|---|
| Opens without LinkedIn login? | **Not verified by design:** the accepted robots early-termination rule stopped before the target request. |
| Target HTTP status/redirects? | **Not verified.** |
| Is ordinary HTTP sufficient? | **Not verified** for content; sufficient for the compliance preflight. |
| Is JavaScript required? | **Not verified.** |
| Fetchers actually tested? | HTTP `FetcherSession`, robots only. |
| Public fields in cards? | **Not verified live.** Synthetic parser covers ID, title, company, location, date, URL. |
| Are detail pages required? | **Open question.** |
| Is full description public? | **Open question.** |
| Job ID extraction? | Fixture-tested from URN, URL, query parameter, numeric attribute. |
| Pagination/lazy loading? | **Not verified live.** Termination logic fixture-tested. |
| No-new-results detection? | Empty set of new stable IDs. |
| Infinite-loop protection? | Maximum pages/requests plus no-new-ID termination. |
| Reliable/optional fields? | **No live reliability claim.** All candidate fields are optional and map to `None`. |

No signs of an authwall, CAPTCHA, rate limit, or target-page blocking can be
reported because the target was intentionally not requested.

## Selectors and fixtures

Candidate selectors are centralized in `spikes/selectors.py`. They are **Not
verified** against current LinkedIn HTML. The only saved fixtures are:

- `tests/fixtures/linkedin/job_cards_synthetic.html`;
- `tests/fixtures/linkedin/job_detail_synthetic.html`.

Both are minimal synthetic HTML, explicitly labeled as such. No complete
LinkedIn response, personal data, cookies, or credentials were saved.

The fixture parser covers card extraction, job ID shapes, null missing fields,
ID/URL duplicate detection, pagination termination, and detail extraction.

## Test and quality results

### Local pagination verification — 2026-08-05

A network-free pagination runner was verified with an injected in-memory source
and three minimal synthetic HTML pages. The sequence contains two new IDs on the
first page, one duplicate plus one new ID on the second page, and only duplicate
IDs on the third page. The runner accumulates IDs, deduplicates cards, counts
pages and source calls, and stops on no new IDs, repeated URLs, identical
content, `max_pages`, or `max_requests`.

This verifies only the local pagination algorithm. Real LinkedIn pagination,
endpoints, parameters, lazy loading, and selectors remain **Not verified**. No
network requests were made during this step, the LinkedIn target was not
requested, and the existing robots preflight was not changed. The Milestone 1
status remains unchanged.

The first focused run was **Fail** (5 passed, 2 failed): it revealed missing
nested description text and incomplete cross-key duplicate handling. Those
issues were fixed. The final results must be read from the final verification
section below.

## Technical and legal limitations

- LinkedIn's retrieved robots file explicitly states automated access without
  express permission is prohibited and points crawlers to a whitelisting path.
- The generic crawler group disallows `/`, including the configured target.
- The project specification also requires respecting robots, terms, privacy,
  and reasonable rates. A technically obtainable response would not override
  this project constraint.
- Python 3.14 is not declared supported by Scrapling 0.4.8.
- Synthetic fixtures prove parser behavior only, not current LinkedIn markup.
- Target status, fields, JavaScript, pagination, details, performance, and
  blocking behavior remain unverified.

## Recommended next step

Do not proceed to Milestone 2 LinkedIn automation. Obtain written permission and
LinkedIn crawler whitelisting, or replace the source with an approved official
API/data feed. A customer-supplied export or another job platform whose terms
and robots permit automated retrieval is also acceptable. Milestone 2 scope must
be reviewed only after that permitted alternative source has been selected and
approved. Only then rerun an appropriate HTTP-first spike and replace synthetic
fixtures with minimal approved fragments.

## Milestone 1 completion criteria

| Criterion | Status | Evidence |
|---|---|---|
| Compliant no-login access evaluated | **Pass** | Accepted early termination: robots preflight prohibited the target request. |
| Tested fetchers/results documented | **Pass** | HTTP robots-only test and exclusions documented. |
| Public job fields disposition recorded | **Pass** | Not verified under the accepted early termination rule; no live claim made. |
| Pagination/detail disposition recorded | **Pass** | Local pagination orchestration is fixture-verified; live LinkedIn pagination and details remain not verified. |
| Counts, timings, failures, limitations recorded | **Pass** | One request, 0.367 s; policy stop recorded. |
| Initial fixture extraction tests pass | **Pass** | Synthetic fixture suite passes in final verification. |
| No access-control circumvention | **Pass** | No target request, stealth, proxy, login, or cookie. |
| `SCRAPLING_GUIDE.md` and `SPIKE_REPORT.md` exist | **Pass** | Both repository documents exist. |

**Milestone 1 is complete under the documented early termination rule.** Live
job-field, pagination, detail-page, JavaScript, and target-response behavior
remain explicitly not verified and must not be presented as extraction evidence.

## Final verification

Final executed repository checks:

- Focused pagination/extraction/preflight tests: **Pass**, 15 passed, 12
  third-party lxml deprecation warnings, 0 failed in 0.20 s.
- Full available suite: **Pass**, 15 passed, the same 12 warnings, 0 failed in
  0.19 s. It includes the local pagination suite and mocked no-network policy
  gate test.
- Ruff: **Pass**, no findings.
- MyPy strict: **Pass**, no issues in 8 source files.
- Repository hygiene: **Pass** for ignored environments/caches/database patterns
  and the absence of stored credentials, cookies, browser profiles, databases,
  or full LinkedIn HTML responses.
