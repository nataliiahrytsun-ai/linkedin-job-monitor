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
