# Scrapling Guide for the LinkedIn Job Monitor

## Status and evidence labels

This guide describes Scrapling 0.4.8, the version installed and inspected on
2026-07-28. Statements about the library are based on the installed package and
the [official documentation](https://scrapling.readthedocs.io/en/latest/). LinkedIn-specific
claims use these labels:

- **Verified:** observed in this repository's executed tests or live preflight.
- **Not verified:** implemented against synthetic HTML only.
- **Assumption:** a proposed future design, not experimental evidence.
- **Open question:** cannot be answered without permission for automated LinkedIn access.

## 1. Overview and project decision

Scrapling is an HTML parsing, HTTP/browser fetching, and asynchronous crawling
framework. It gives a fetched response the same CSS/XPath selection interface as
an offline `Selector`. The project selected it because one library can support a
small HTTP probe now and a bounded Spider crawl later, if compliant access is
obtained.

**Verified:** this milestone uses `FetcherSession` for a single public
`robots.txt` request and `Selector` for offline fixture parsing. No target job
page was requested because LinkedIn's current `User-agent: *` rule disallows `/`.

The following are not used in this project at Milestone 1:

- `StealthyFetcher`, CAPTCHA solving, fingerprint hiding, proxies, or browser
  impersonation. These conflict with the project's no-circumvention boundary.
- `DynamicFetcher`. JavaScript need could not be assessed after the robots gate.
- adaptive selectors. They persist element fingerprints and can conceal a
  structural change; explicit, reviewed fallbacks are safer for job data.
- async fetching and Spider execution. A one-request preflight gains nothing
  from concurrency.
- streaming and pause/resume. They are useful for a permitted large crawl, not
  for the stopped spike.

## 2. Installation and dependencies

Scrapling 0.4.8 declares Python 3.10 through 3.13 in its package metadata. Use
Python 3.12 for the project:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-spike.txt
```

`scrapling` alone contains parsing dependencies. The `[fetchers]` extra adds the
HTTP engine, AnyIO/Spider support, Playwright/Patchright, and browser-related
packages. It is pinned because the API changed substantially in the 0.4 series.

If a permitted use later requires a browser, install its binaries separately:

```powershell
.\.venv\Scripts\scrapling.exe install
```

The official image is an alternative for isolated Linux execution:

```text
docker pull ghcr.io/d4vinci/scrapling:latest
```

Pin a digest before repeatable deployment. Docker is not used in this milestone
and does not solve legal, robots, or permission constraints.

**Environment limitation:** the repository's pre-existing `.venv` points to a
missing Python 3.12.6 installation. Verification therefore used an ignored
`.spike-venv` with Python 3.14.0. Installation and tests succeeded, but 3.14 is
outside Scrapling 0.4.8's declared Python classifiers.

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
2. If prohibited, stop cleanly. Do not fetch the target.
3. If permission and robots allow access, test the plain HTTP session first.
4. Use `AsyncFetcher` only after several independent detail requests show a
   measured latency benefit, with bounded concurrency.
5. Consider `DynamicFetcher` only if allowed HTTP responses omit data that a
   normal public browser shows solely after JavaScript execution.

**Verified:** only `FetcherSession` was actually tested, and only against
`https://www.linkedin.com/robots.txt`. It returned HTTP 200 without redirect.

**Not verified:** ordinary target HTTP, async target HTTP, browser rendering,
job-card/detail extraction, performance differences, and session reuse across
multiple target requests. `StealthyFetcher` must not be tested for LinkedIn.

## 5. LinkedIn-specific extraction design

### Entry point and access gate

The company job URL is configuration, never Acuity-specific parser logic. Before
every live crawl, validate HTTPS and the `linkedin.com` host, fetch robots, and
stop if the user agent cannot fetch the URL. The current spike does the robots
gate but production URL validation is Milestone 2 scope.

### Cards and details

**Not verified:** the synthetic fixture exercises candidate job-card fields:
job ID, title, company, location, publication date, and absolute job URL. The
synthetic detail fixture exercises description, seniority, employment type, job
function, and industry. A missing selector returns `None` and never crashes.

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

This verifies the orchestration algorithm only. Real LinkedIn pagination,
pagination URLs, page size, lazy loading, and current selectors remain **Not
verified**. This local verification made no network requests and did not alter
the robots preflight.

### Fallbacks and HTML-change detection

Candidate selectors are ordered broad alternatives in one module. A future live
fixture must validate each selector before promotion to production. Treat any of
these as a structural-change signal:

- HTTP 200 but zero cards where a previous successful run had jobs;
- cards without both ID and URL;
- a sharp increase in null required fields;
- an authwall, checkpoint, CAPTCHA, or consent page signature;
- duplicate pagination content.

Do not use adaptive selection to turn such a signal into an unreviewed match.

## 6. Conservative performance configuration

The spike configuration is explicit:

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

## 7. Testing and diagnostics

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
2. Confirm robots and permission before looking at HTML.
3. Distinguish network timeout from HTTP error and policy denial.
4. Detect authwall/checkpoint/CAPTCHA explicitly; never solve or bypass it.
5. If selectors return no jobs, compare a minimal approved fixture and selector
   counts; do not save an unnecessary full page.
6. If a browser fails, verify `[fetchers]` and `scrapling install`, but only run
   it when JavaScript use is permitted and justified.
7. A single permitted detail failure should be recorded and skipped; it must not
   abort the company run.

Likely blockers are robots denial, lack of express crawl permission, 403/429,
authwall/checkpoint, changed HTML, and JavaScript-only content. Only the first
two were confirmed in this milestone (robots text directs crawlers to request
whitelisting; no permission was supplied).

## 8. Extension path

- **New company:** store a name and validated public jobs URL; run the same
  access preflight. No selector may contain Acuity-specific IDs.
- **Another platform:** add a platform adapter with its own URL validator,
  robots gate, selectors, fixtures, and field mapping. Keep normalization and
  persistence independent.
- **Selector update:** capture the smallest approved/redacted fragment, add a
  failing fixture test, update only the centralized selector module, and record
  evidence/date.
- **Scheduler:** after Milestone 2 proves safe execution, add the smallest
  controlled periodic trigger. Prevent overlapping company runs. Celery/Redis
  are not justified by this milestone.
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
