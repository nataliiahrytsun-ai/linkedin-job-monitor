# Milestones

## Milestone 1 — Scrapling Research and LinkedIn Technical Spike

**Status:** Completed — Not feasible through compliant public access

### Scope

- Analyze the Scrapling repository and official documentation.
- Create `docs/SCRAPLING_GUIDE.md`.
- Test the public Acuity Analytics LinkedIn Jobs URL.
- Determine which Scrapling fetcher is suitable.
- Investigate pagination, lazy loading, and job detail pages.
- Save appropriate HTML fixtures.
- Add initial extraction tests.
- Create `docs/SPIKE_REPORT.md`.
- Make a documented feasibility decision:
  - feasible;
  - feasible with limitations;
  - not feasible through compliant public access.

### Early termination rule

The technical spike may terminate before requesting the configured target page
when the live `robots.txt` policy disallows that target for the spike's ordinary
crawler user agent. This is an acceptable and complete spike outcome when all of
the following are true:

- `robots.txt` was fetched without login, authenticated cookies, proxies, or
  access-control circumvention;
- the applicable rule and tested target URL were evaluated and recorded;
- the target page, pagination endpoints, and detail pages were not requested;
- request counts, timings, environment, tested fetcher, and policy limitation
  were documented;
- the feasibility decision is
  `Not feasible through compliant public access`;
- offline fixture-based parsing and policy-gate tests pass.

Under this rule, target fields, pagination, JavaScript requirements, and detail
page behavior are recorded as not verified because compliant access ended the
experiment before those checks. They are not failed completion criteria and no
live extraction claim may be made.

### Completion criteria

- The target page has been tested without LinkedIn login or authenticated
  cookies, or the documented early termination rule has applied before the
  target request.
- The tested fetchers and their results are documented.
- Publicly available job fields are identified when target access is permitted;
  otherwise their unverified status is recorded under the early termination
  rule.
- Pagination and detail-page behaviour are documented when target access is
  permitted; otherwise their unverified status is recorded under the early
  termination rule.
- Request counts, timings, failures, and limitations are recorded.
- Initial fixture-based extraction tests pass.
- No access-control circumvention has been implemented.
- `docs/SCRAPLING_GUIDE.md` and `docs/SPIKE_REPORT.md` exist.

**Completion outcome:** the early termination rule applied. LinkedIn's live
`robots.txt` disallowed the Acuity Analytics target for the ordinary crawler,
the runner stopped before the target request, and the result was documented as
`Not feasible through compliant public access`.

### Limited pagination follow-up and one corrective rerun

The completed outcome above remains the historical Milestone 1 result. The
first manually confirmed limited live pagination run used exactly:
`https://www.linkedin.com/jobs/acuity-analytics-jobs-worldwide?f_C=16691%2C30242966`.

Its current robots preflight made one request to `robots.txt`, returned HTTP
200, recorded `target_allowed=false`, and did not request the target page. With
`--confirm-live-test`, the live runner then made exactly one target request,
received HTTP 200 with no redirects, found no Job IDs, made no next request, and
recorded `stop_reason="captcha"`.

The result is **Inconclusive** — the response was classified as CAPTCHA by the
previous broad raw-HTML marker check, but the saved report does not contain
enough evidence to confirm that a CAPTCHA was actually presented. The defective
classifier matched `captcha` or `security verification` anywhere in raw HTML,
including possible JavaScript, metadata, resource URLs, or hidden text. Commit
`7613ef9d8bdcc8ac252047d61d7aa46edd2d4318` replaced that check with
structural CAPTCHA diagnostics and added safe `block_reason` and
`block_evidence` report fields. Real LinkedIn pagination remains **Not
verified**.

Under the existing team instruction to test pagination locally with only a few
requests, exactly one corrective diagnostic rerun is permitted because the
first run did not produce reliable pagination evidence. It must:

- create a new current robots preflight and record its result;
- use the same exact target URL above and require `--confirm-live-test`;
- make at most 4 target requests across at most 4 job-listing pages;
- run sequentially with at least 2 seconds between requests;
- use no login, cookies, proxy, IP rotation, stealth, browser fetcher,
  impersonation, retry, detail-page request, or saved full HTML response;
- terminate without another request on no confirmed next-page link, no new Job
  ID, repeated URL/content, or a page/request limit;
- terminate immediately without another request on HTTP 401, 403, or 429, a
  login/authwall/checkpoint redirect, confirmed CAPTCHA, access denied,
  consent/interstitial content, or any other confirmed technical block.

This limited corrective rerun does not change the Milestone 1 status, represent
the rerun as completed, or permit additional reruns, Milestone 2 LinkedIn
scraping, production execution, full server-side scraping, or circumvention.
Any production use still requires a separate team decision. Historical
diagnostic JSON files must remain unchanged.

---

## Milestone 2 — Backend and Scraping Pipeline

**Status:** Provisional — review after selecting a permitted alternative data source

### Expected scope

- Django project and applications.
- Database models and migrations.
- Extraction, normalization, and deduplication.
- Persistence and job status updates.
- `ScrapeRun` history.
- Controlled background execution.
- Backend unit and integration tests.

Do not begin this milestone against LinkedIn under the current access result.
Its final scope and acceptance criteria must be reviewed after selecting and
approving a permitted alternative data source, such as an official API, approved
feed, customer-supplied import, or another platform that permits automated
retrieval.

---

## Milestone 3 — UI and Final Verification

**Status:** Provisional — refine after Milestone 1

### Expected scope

- Dashboard.
- Company management.
- Job list, detail view, and filters.
- Scraping controls and status polling.
- Error and scrape-run display.
- Complete automated tests.
- Manual verification.
- Final README and project documentation updates.

The final scope and acceptance criteria will be defined after Milestone 1.
