# Scrapling Guide for the LinkedIn Job Monitor

## Status and evidence labels

This guide describes Scrapling 0.4.8, the version installed and inspected on
2026-07-28. Statements about the library are based on the installed package and
the [official documentation](https://scrapling.readthedocs.io/en/latest/). LinkedIn-specific
claims use these labels:

- **Verified:** observed in this repository's executed tests, live preflight, or
  a limited live diagnostic under the existing team instruction.
- **Not verified:** not established by reliable live evidence; it may have been
  implemented or tested against synthetic HTML only.
- **Assumption:** a proposed future design, not experimental evidence.
- **Open question:** not answered by the completed limited diagnostics, such as
  production-scale continuation behavior or complete vacancy collection.

## 1. Overview and project decision

Scrapling is an HTML parsing, HTTP/browser fetching, and asynchronous crawling
framework. It gives a fetched response the same CSS/XPath selection interface as
an offline `Selector`. The project selected it because one library can support a
small HTTP probe now and a bounded Spider crawl later, if compliant access is
obtained.

**Current production decision (2026-08-11):** the source-neutral application
uses `LeverSourceAdapter` as its first production adapter. It uses Scrapling
0.4.8 `FetcherSession` for bounded plain-HTTP requests to Lever's public
postings API. The registered fixture adapter is internal/test-only and makes no
network requests. The Darwinbox adapter uses `DynamicFetcher` with a temporary
normal headful system-Chrome session because its public SPA did not bootstrap
in the verified headless flow. It disables Scrapling's Google referrer and uses
no stealth, profile/cookies, proxy, custom headers/user agent, or fingerprint
override. There is no production LinkedIn adapter; the LinkedIn
sections below document the completed historical spike and its safety boundary.

**Verified:** the original milestone used `FetcherSession` for a single public
`robots.txt` request and `Selector` for offline fixture parsing. No target job
page was requested during that original run because LinkedIn's current
`User-agent: *` rule disallows `/`. Later limited diagnostics ran under the
existing team instruction. The extraction validation verified live extraction;
the single post-fix continuation validation later verified pagination within
its strict four-request scope.

The following are not used in this project at Milestone 1:

- `StealthyFetcher`, CAPTCHA solving, fingerprint hiding, proxies, or browser
  impersonation. These conflict with the project's no-circumvention boundary.
- `DynamicFetcher` for LinkedIn. It is used only by the Darwinbox adapter's
  explicitly headful public-SPA transport.
- adaptive selectors. They persist element fingerprints and can conceal a
  structural change; explicit, reviewed fallbacks are safer for job data.
- async fetching and Spider execution. A one-request preflight gains nothing
  from concurrency.
- streaming and pause/resume. They are useful for a permitted large crawl, not
  for the stopped spike.

## 2. Installation and dependencies

Scrapling 0.4.8 is pinned with the `[fetchers]` extra in the production
requirements. Create the environment and install normal application
dependencies with:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Install `requirements-dev.txt` instead when running tests and quality checks;
it includes the runtime requirements and pins pytest, Ruff, and MyPy.
`requirements-spike.txt` remains only for the historical LinkedIn diagnostic
environment and is not the production installation path.

`scrapling` alone contains parsing dependencies. The `[fetchers]` extra adds the
HTTP engine, AnyIO/Spider support, Playwright/Patchright, and browser-related
packages. It is pinned because the API changed substantially in the 0.4 series.

If a permitted use later requires a browser, install its binaries separately:

```powershell
.\.venv\Scripts\scrapling.exe install
```

The current Darwinbox transport uses `real_chrome=True`, so it requires an
installed system Google Chrome and an interactive desktop session. This is an
explicit runtime prerequisite; server/headless deployments are not silently
substituted with stealth or direct API access.

The official image is an alternative for isolated Linux execution:

```text
docker pull ghcr.io/d4vinci/scrapling:latest
```

Pin a digest before repeatable deployment. Docker is not used in this milestone
and does not solve legal, robots, or permission constraints.

The previously broken environment that referenced a missing Python 3.12.6
interpreter was replaced during dependency maintenance. The verified local
environment used Python 3.14.0 and successfully imported
`scrapling.fetchers.FetcherSession`; a clean setup should still prefer a Python
version declared compatible by the pinned dependencies.

## 3. Scrapling architecture and confirmed API

### Fetchers and sessions

The installed API exposes:

- `Fetcher.get/post/put/delete` and their `AsyncFetcher` equivalents for normal
  HTTP. In 0.4.8 the method is `get`, not `fetch`.
- `FetcherSession(...)` as a sync or async context manager. It keeps a curl
  session alive and reuses connections.
- `DynamicFetcher.fetch/async_fetch` and `DynamicSession` for JavaScript via
  Playwright.
- `StealthyFetcher` and related sessions for bypass-oriented browser behavior;
  deliberately excluded here.

`FetcherSession` accepts timeout, attempts (`retries` in the API), retry delay,
redirect policy, headers, TLS impersonation, and proxies. The spike disables
generated stealth headers and impersonation and sends a descriptive user agent.

**Installed-version caveat:** internally, `range(retries)` controls attempts, so
`retries=0` performs no request and raises `RuntimeError`. The project calls its
setting `max_attempts_per_request` and passes at least 1.

### Request, Response, and Selector

A Spider `Request` carries URL, callback, method/body, priority, metadata, and a
session ID. The scheduler fingerprints URL, method, body, and session ID for
deduplication. A `Response` contains status, URL, redirect history, headers/body,
metadata, and inherited `Selector` methods.

`Selector(content=..., url=...)` parses saved HTML without network traffic.
`css()` supports CSS3 plus `::text` and `::attr(name)`; `xpath()` is backed by
lxml. `.get()` returns the first serialized/text result and `.getall()` returns
all results. This project keeps CSS expressions in `spikes/selectors.py`.

### Spider, concurrency, statistics, streaming, and checkpoints

Spider is an AnyIO-based crawling layer. It schedules `Request` objects, routes
them through reusable sessions, applies global and per-domain concurrency,
download delay, optional robots enforcement, duplicate filtering, retry/block
handling, and records crawl statistics. Results can be collected by `start()` or
consumed incrementally with `async for ... in spider.stream()`.

With a `crawldir`, graceful stop saves pending requests and fingerprints so a
crawl can resume. These capabilities are technically supported but **not used**
until automated access is permitted and a multi-page crawl is justified.

### Adaptive selectors

Adaptive selection stores structural properties for a known element and tries
to relocate it after markup changes. It requires `adaptive=True`, a stable URL
domain, storage, and an initial saved match. It is **consciously excluded** from
the first implementation: a low-confidence relocation could silently map the
wrong field. Explicit selectors, fixture failures, zero-result alarms, and a
manual selector review are preferred.

## 4. Fetcher selection strategy and tested fetchers

The selection order is:

1. Fetch and evaluate `robots.txt` with a plain `FetcherSession`.
2. Record the robots result in the diagnostic output. For ordinary operation,
   stop cleanly if prohibited and do not fetch the target.
3. Only for a local pagination diagnostic under the existing team instruction,
   treat `Disallow: /` as a warning and continue only when
   `--confirm-live-test` is present and all limits below are enforced.
4. Use only the plain HTTP session for that diagnostic.
5. Use `AsyncFetcher` only after several independent detail requests show a
   measured latency benefit, with bounded concurrency.
6. Consider `DynamicFetcher` only if allowed HTTP responses omit data that a
   normal public browser shows solely after JavaScript execution.

**Verified:** `FetcherSession` was tested against
`https://www.linkedin.com/robots.txt`, which returned HTTP 200 without redirect.
The first, corrective, and final limited diagnostics used ordinary target HTTP.
The final validation received HTTP 200 without redirects and extracted 60
unique LinkedIn Job IDs.

**Not verified live:** async target HTTP, detail extraction, performance
differences, and session reuse across multiple target requests.
`StealthyFetcher` must not be tested for LinkedIn.

## 5. Lever production adapter

`LeverSourceAdapter` is the current production use of Scrapling. It receives a
CompanySource configuration, validates a URL of the form
`https://jobs.lever.co/<site>`, derives the site slug, and requests Lever's
public postings API with `mode=json`, a bounded `limit`, and an offset `skip`.
It maps each response to the shared `SourceRecord` contract and returns one
combined `SourceBatch`; normalization and persistence do not contain
Lever-specific branches.

Pagination starts at `skip=0`, advances by the page size, and deduplicates
posting IDs. A short/empty page completes the snapshot; a page without new IDs
or reaching the configured safety page limit fails cleanly instead of looping.
`requests_made` counts attempted HTTP calls and is carried through successes
and source errors into `ScrapeRun`.

**Verified offline through the production flow:** with `limit=2`, fake HTTP
served two existing Lever JSON fixtures at `skip=0` and `skip=2`. The registry,
background executor, adapter, shared pipeline, normalization, persistence, and
reconciliation produced three jobs and a terminal `SUCCESS` run with
`requests_made=2`. Automated tests do not call live Lever.

Historical manual verification of the Olo company established the live Lever
path separately. It is not repeated by this documentation closeout.

## 5a. JazzHR production adapter

`JazzHRSourceAdapter` uses Scrapling 0.4.8 `FetcherSession` for ordinary public
HTTP against a validated `<tenant>.applytojob.com` host. A canonical `/apply`
listing request discovers current or legacy detail URLs; their opaque URL token
is the stable ID. One required detail request per unique ID prefers an
unambiguous `JobPosting` JSON-LD object. If no JobPosting candidate exists, a
strict server-rendered fallback requires `.job-header`, one matching `h2`,
`.job-attributes-container`, and non-empty `#job-description` content outside
the application form. The visible Ref is not identity: the Acuity/Ascent audit
found duplicate Ref `26521` on distinct jobs.

No explicit total count is published. Completeness means the server-rendered
listing contains verifiable job structure, exposes no unhandled pagination,
and every unique discovered detail succeeds. The adapter returns no partial
batch. `requests_made` is one listing attempt plus each detail attempt.

Robots preflight returned HTTP 200; the wildcard group disallowed `/cb`, not
`/apply`. The bounded audit found HTTP 200, server-rendered links, no pagination
control, 23 opaque IDs, and detail JSON-LD with description, location,
employment type, and dates. Another HTTP-200 detail exposed only Organization
JSON-LD; a single structural diagnostic confirmed a complete
`#job-description` outside `#job-application-form-container`. Sanitized offline
fixtures verify this fallback, application/CAPTCHA exclusion, canonical ID
validation, and fail-closed ambiguity handling. The final bounded live run
returned 23 records for 23 opaque IDs in 24 requests: 6 details used JSON-LD
and 17 used HTML fallback, with no challenge, login, or access-denied response.
Manual UI execution succeeded; a repeat run found 23 jobs and created or
updated none. This proves only the configured public interim source, not every
Acuity vacancy or every JazzHR tenant.

## 6. LinkedIn-specific extraction design (historical spike)

### Entry point and access gate

The company job URL is configuration, never Acuity-specific parser logic. Before
every live crawl, validate HTTPS and the `linkedin.com` host, fetch robots, and
record the result. Ordinary and production runs stop if the user agent cannot
fetch the URL. The only exception is the explicitly confirmed limited local
pagination diagnostic defined below; production URL validation remains
Milestone 2 scope.

### Cards and details

**Verified offline:** the synthetic fixtures exercise candidate job-card fields:
job ID, title, company, location, publication date, and absolute job URL. An
inspected real rendered DOM fragment is also parsed offline as one card with Job
ID `4447661197`, title `Delivery Manager`, and its regional LinkedIn URL. The
final plain-HTTP validation extracted 60 unique Job IDs including `4447661197`.
The synthetic detail fixture exercises description, seniority, employment type,
job function, and industry. A missing selector returns `None` and never crashes.

Descriptions and criteria are modeled as detail-page fields because result cards
usually cannot be assumed to contain full descriptions. Whether separate detail
requests are necessary remains an **Open question**.

### LinkedIn Job ID and duplicates

The fixture-tested parser recognizes:

- `urn:li:jobPosting:<digits>`;
- a numeric ID in `/jobs/view/...-<digits>` or `/jobs/view/<digits>`;
- `currentJobId` or `jobId` query parameters;
- an all-numeric ID attribute.

Deduplication prefers the LinkedIn ID and also records a normalized URL without
query/fragment as a fallback. This is fixture-verified, not live-verified.

### Pagination and lazy loading

**Open question:** the real endpoint, parameters, page size, and lazy-loading
behavior were not requested after the robots denial. Candidate termination is
now verified locally with three synthetic pages and an injected in-memory page
source. The runner accumulates stable job IDs across pages, deduplicates cards,
counts fetched pages and source calls, and stops on no new IDs, a repeated URL,
identical content, `max_pages`, or `max_requests`.

Commit `5096e5a901220149916685660fdf1cba50c1231d` adds a validated
`seeMoreJobPostings` continuation URL derived only from the confirmed company
jobs path, `f_C`, and explicit `start`/step configuration. Synthetic/offline
tests verify global Job ID deduplication, continuation after one or two
overlap-only batches, reset after a batch with a new ID, `overlap_limit` on the
third consecutive overlap, empty/repeated response termination, and the hard
4-request/4-page limits. These tests made no network requests.

Full LinkedIn pagination, the actual offset sequence beyond the configured
diagnostic values, page size, and lazy loading remain **Not verified**.

### Limited pagination diagnostics closeout

The canonical closeout is
[`docs/diagnostics/linkedin-pagination-2026-08-05.md`](diagnostics/linkedin-pagination-2026-08-05.md).
**Live extraction is Verified; full live pagination is Verified for the limited
diagnostic.** The
continuation endpoint was observed manually and is now implemented and verified
synthetically/offline. Exactly one limited post-fix validation then made 4
requests for 4 pages, used offsets 25, 50, and 75, received HTTP 200 without
redirects, and added 22 IDs outside the saved initial 60-ID baseline. It stopped
at `page_limit` with no technical block. No additional live run or production
use is permitted.

### Fallbacks and HTML-change detection

Known card-container selectors remain the primary path. Commit
`b852de18d195df795bbfcc28c7b573b164702853` added a fallback for standalone
`a[href*="/jobs/view/"]` links only when the resolved URL has a LinkedIn host and
a numeric Job ID. Titles fall back from existing selectors to `span.sr-only`,
`aria-label`, and cleaned link text; results are deduplicated by Job ID. The
final plain-HTTP validation confirmed live extraction through this parser. Treat
any of these as a structural-change signal:

- HTTP 200 but zero cards where a previous successful run had jobs;
- cards without both ID and URL;
- a sharp increase in null required fields;
- an authwall, checkpoint, CAPTCHA, or consent page signature;
- duplicate pagination content.

Do not use adaptive selection to turn such a signal into an unreviewed match.

## 7. Conservative performance configuration

The completed 2026-07-28 robots-only preflight used this configuration; it is
historical evidence rather than the later diagnostic configuration:

| Setting | Value | Meaning |
|---|---:|---|
| Timeout | 20 s | Per request |
| Request delay | 2 s | Reserved for any permitted follow-up request |
| Max pages | 2 | Hard experimental bound |
| Max requests | 3 | Includes list/detail requests, robots recorded separately by report |
| Concurrency | 1 | Sequential spike |
| Attempts | 1 | No retry amplification |
| Redirects | `safe` | Follow only SSRF-safe redirects |

**Assumption for a permitted later crawl:** reuse one session, keep per-domain
concurrency at 1 initially, add bounded exponential backoff only for transient
timeouts/5xx, and never retry 401/403/429 aggressively. Expected target runtime
is **Not verified**.

The completed post-fix validation retained the separate fixed limits of at most 4
pages and 4 target requests, concurrency 1, at least a 2-second delay, one
attempt with no retry, and no followed technical-block continuation.

## 8. Testing and diagnostics

Normal tests instantiate `Selector` from minimal synthetic fixtures. Pagination
tests use an injected in-memory source backed by three synthetic HTML files; the
runner has no HTTP dependency and makes no network calls. Live preflight remains
a separate command:

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m spikes.linkedin_spike
```

The second command returns exit code 2 when robots denies the target. That is a
controlled policy stop, not a parser crash.

Diagnostic order:

1. Record URL, status, final URL, redirect count, timing, and request count.
2. Check and record robots. For the limited pagination diagnostic, a denial is a
   warning and continuation requires `--confirm-live-test`; ordinary operation
   still stops.
3. Distinguish network timeout from HTTP error and policy denial.
4. Detect authwall/checkpoint/CAPTCHA explicitly; never solve or bypass it.
5. If selectors return no jobs, compare a minimal approved fixture and selector
   counts; do not save an unnecessary full page.
6. If a browser fails, verify `[fetchers]` and `scrapling install`, but only run
   it when JavaScript use is permitted and justified.
7. A single permitted detail failure should be recorded and skipped; it must not
   abort the company run.

Likely blockers are robots denial, lack of express crawl permission, 403/429,
authwall/checkpoint, changed HTML, and JavaScript-only content. At the time of
the completed milestone, the first two were confirmed (robots text directs
crawlers to request whitelisting; no permission had been supplied). The
existing team instruction covered the inconclusive first diagnostic, the
corrective diagnostic, the completed extraction validation, and exactly one
completed limited post-fix continuation validation. It does not authorize an
additional live run or production use.

## 9. Extension path

- **New Lever company:** configure a CompanySource with `source="lever"` and a
  validated public URL in the form `https://jobs.lever.co/<site>`. The shared
  pipeline uses the one registered production adapter; a separate adapter per
  Company is not required.
- **Another approved platform:** implement the shared adapter contract with its
  own URL validation, fetching policy, fixtures, and field mapping; register it
  explicitly as user-selectable only when it is a production source. Keep
  normalization and persistence independent.
- **LinkedIn:** treat the current artifacts as historical spike evidence, not
  an adapter template that authorizes production use. Resolve feasibility and
  access first; only then can a separately reviewed adapter be considered.
- **Selector update:** capture the smallest approved/redacted fragment, add a
  failing fixture test, update only the centralized selector module, and record
  evidence/date.
- **Scheduler:** if scheduling is later approved, add the smallest controlled
  periodic trigger. Preserve the implemented source-level overlap protection;
  Celery/Redis are not justified by the current scope.
- **SQLite to PostgreSQL:** keep Django ORM models database-neutral, avoid
  SQLite-specific SQL, test migrations and uniqueness constraints on the target
  database. This is future scope, not implemented here.

## Sources reviewed

- [Scrapling repository and release history](https://github.com/D4Vinci/Scrapling)
- [Fetcher choice](https://scrapling.readthedocs.io/en/latest/fetching/choosing.html)
- [HTTP fetching](https://scrapling.readthedocs.io/en/latest/fetching/static.html)
- [Dynamic fetching](https://scrapling.readthedocs.io/en/latest/fetching/dynamic.html)
- [Selection](https://scrapling.readthedocs.io/en/latest/parsing/selection.html)
- [Adaptive selection](https://scrapling.readthedocs.io/en/latest/parsing/adaptive.html)
- [Spider architecture](https://scrapling.readthedocs.io/en/latest/spiders/architecture.html)
- [Requests and responses](https://scrapling.readthedocs.io/en/latest/spiders/requests-responses.html)
- [Spider sessions](https://scrapling.readthedocs.io/en/latest/spiders/sessions.html)
- [Concurrency, statistics, streaming, pause/resume](https://scrapling.readthedocs.io/en/latest/spiders/advanced.html)
