# Production Source Discovery

Source Discovery is an independent orchestration layer. It locates a probable
official company site, follows a bounded set of careers links, detects an ATS,
and records an auditable result. It does not scrape vacancies, run
reconciliation, or modify existing job postings.

## Flow and ownership

`DiscoveryService` coordinates an injected `SearchProvider`, `BoundedCrawler`,
source-neutral platform catalog, adapter URL validation, and persistence. `DiscoveryRun`
stores one attempt and its terminal state. `DiscoveryCandidate` stores safe URL,
confidence, evidence, redirects, support/decision state, and an optional link to
the resulting `CompanySource`. Full HTML and credentials are never persisted.

The production provider is Tavily Search's structured JSON endpoint. Normal
production and development operation requires a Tavily API key:

```text
SOURCE_DISCOVERY_SEARCH_PROVIDER=tavily
SOURCE_DISCOVERY_TAVILY_API_KEY=<secret>
SOURCE_DISCOVERY_TAVILY_KEYLESS_DIAGNOSTIC=false
SOURCE_DISCOVERY_SEARCH_TIMEOUT_SECONDS=10
SOURCE_DISCOVERY_SEARCH_RETRIES=1
SOURCE_DISCOVERY_SEARCH_MAX_QUERIES=10
SOURCE_DISCOVERY_SEARCH_MAX_RESULTS=6
SOURCE_DISCOVERY_SEARCH_MAX_RESPONSE_BYTES=1000000
SOURCE_DISCOVERY_TOTAL_TIMEOUT_SECONDS=45
SOURCE_DISCOVERY_MAX_REQUESTS=8
SOURCE_DISCOVERY_MAX_DEPTH=2
SOURCE_DISCOVERY_MAX_REDIRECTS=4
SOURCE_DISCOVERY_MAX_BODY_BYTES=2000000
SOURCE_DISCOVERY_TIMEOUT_SECONDS=10
```

Normal requests send only `Authorization: Bearer <secret>`. The key is never
persisted or included in application messages. Missing key configuration fails
closed before a request is sent. Keyless access is retained only for explicitly
bounded diagnostics by setting
`SOURCE_DISCOVERY_TAVILY_KEYLESS_DIAGNOSTIC=true`; it sends only
`X-Tavily-Access-Mode: keyless` and must not be the ordinary production/dev
mode. Provider rate limiting fails closed with `TavilyRateLimitError`. An
operator may explicitly select the legacy Brave implementation with
`SOURCE_DISCOVERY_SEARCH_PROVIDER=brave` and
`SOURCE_DISCOVERY_BRAVE_API_KEY=<secret>`. There is no automatic provider
fallback because a partial or ambiguous fallback could change the identity
decision unexpectedly.

Name-only discovery starts with two fixed basic searches, `"<company>" official
website` and `"<company>" official careers jobs`. It then inventories existing
sources and retained validated candidates, scans the official/careers pages,
and searches only registered adapters that are still missing. Each adapter
owns discovery hints (hosts, URL patterns, technical signals, canonicalization,
tenant identity, and one bounded search hint). A blocked official page may use
one additional `site:<official-domain> careers jobs` request. Results are cached
within the run. The default shared budget is seven searches shared by two general
queries, the three bounded inventory queries, adapter-specific checks, and the
403 fallback. Exhausted coverage remains explicitly `not_checked`.
Each response excludes
answers, raw content, and images. Only URL, title, content snippet, and optional
score cross the provider boundary. Search time, total attempts, query count,
results per query, accepted JSON bytes, crawl requests, redirects, page bytes,
and the total orchestration time are bounded. Empty results, timeouts, invalid
JSON, oversized responses, rate limits, and HTTP failures create a durable
failed run without choosing a site or creating a source. A manually supplied
official domain bypasses search and remains subject to the same crawler safety
checks. `StaticSearchProvider` supports deterministic offline use.

If the selected official site cannot be fetched publicly, including HTTP 401,
403, or 429, Discovery retains the official candidate and records the safe
fetch reason. It does not retry with stealth, browser impersonation, proxies,
cookies, or access-control workarounds. It reuses the already saved
`"<company>" official careers jobs` results and may issue the one bounded
`site:<official-domain> careers jobs` query. These results are candidate evidence,
not proof of page content. Social/job-board results are rejected; registered
ATS URLs must both pass the detector/adapter URL contracts and carry company
identity evidence. Search-fallback sources always require review unless they
already match an existing source, whose approval and active state are
preserved. Partial useful results remain reviewable if a later fallback query
is rate-limited.

## Platform inventory and adapter availability

Platform classification is intentionally separate from the adapter registry.
The catalog identifies only direct, public vendor URL signatures and does not
infer a platform from a vendor word in page text, metadata, JavaScript, or a
search snippet. The current catalog recognizes Lever, Darwinbox, JazzHR,
DreamJobs, Zoho Recruit, Workday, Greenhouse, Personio, SmartRecruiters,
Workable, Ashby, and Teamtailor. The registry answers the separate question of whether a validated,
executable adapter exists.

Unsupported catalog detections are persisted as source candidates with their
canonical listing/root URL, confidence, and URL-signature evidence. They are
published in the Discovered inventory by their classified candidate state, not
by a fragile per-platform evidence-string whitelist, so persisted Personio,
Workday, Greenhouse, SmartRecruiters, Workable, Ashby, and Teamtailor
candidates remain visible as **Adapter not implemented** and still cannot
create a `CompanySource`. A credible public careers URL with no catalog match is
retained once per host as **Unknown / Custom**, rather than being hidden as no
source found. Detail, application, privacy, login, and social URLs are not
separate inventory sources. Discovery retains every distinct credible platform
candidate in a run; automatic connection remains restricted to one validated
registered adapter under the existing threshold rules.

Crawler ATS detections are attributed conservatively. A known ATS URL is kept
only when it is observed on the confirmed official site or on a first-hop
careers destination linked from that site, and when the canonical ATS tenant or
path identity is compatible with the company or official-domain identity. This
preserves legitimate cases such as `jobs.smartrecruiters.com/CERN` while
rejecting unrelated tenants linked from the same external careers page.

Candidate URLs are sanitized before persistence, presentation, and connection.
Fragments, explicit tracking parameters, trailing-slash variants, and bounded
listing pagination forms such as `?page=2`, `?p=2`, and `/page/2/` collapse to
one logical source. Registered and catalogued ATS candidates additionally use
their platform/tenant identity. The deterministic winner prefers a supported,
clean canonical listing route. Equivalence remains conservative: unknown query
parameters, different careers subpaths or business units, different ATS tenants,
and an unproven first-party landing page plus ATS source remain distinct.

For bounded diagnostics, Discovery also records compact safe provenance notes on
the selected official-site candidate when it observes a first-hop careers/source
URL on the official site, prefers a first-hop external redirect destination over
an intermediate official URL, or ignores second-hop careers-like links from an
external careers page. These notes help explain why a URL was retained or
excluded without storing full HTML.

When name-only Discovery cannot initialize its configured search provider, the
UI gives safe guidance to supply the official domain (which bypasses search) or
configure the approved provider. It does not expose credentials or raw provider
errors.

## Confidence and decisions

Official-site confidence is based on normalized company/domain identity and
search-result title evidence, followed by homepage title/metadata/text and its
relationship to a careers link. Social networks, search engines, LinkedIn job
search, generic job boards, aggregators, and hosted ATS domains are retained as
rejected candidates with a safe reason; they can never become the official
website. Deep search results are classified through their origin rather than
blindly treated as the company homepage. Platform confidence comes from detector-owned
technical signals and the existing adapter's canonical URL validator.

Automatic connection requires all of the following:

- exactly one credible official-site candidate, with confidence at least 80;
- exactly one supported platform candidate, with confidence at least 90;
- a careers/source URL observed in the bounded crawl;
- the platform key present in the existing source registry;
- successful validation by the matching adapter URL contract.

The resulting new `CompanySource` is created with `get_or_create`, approved and
active, matching the existing manual-source workflow. A repeat run returns
`already_connected` and preserves the existing active/approval state; blocked
or rejected sources are never silently re-approved. Multiple official domains,
multiple supported sources, or a supported signal below 90 produce separate
`needs_review` candidates and no automatic source. Discovery never stops after
its first ATS match. Manual confirmation revalidates the URL before connection. Unsupported
or unknown careers pages are retained as candidates with an explicit new-adapter
action; no fictitious source is created.

## Crawl safety and limits

The crawler uses one active ordinary Scrapling `FetcherSession` for the complete
bounded crawl and copies each response while that session is still active. The
context is closed on success, timeout, and exceptions. It uses no login, cookies, browser,
stealth, impersonation, proxy, or CAPTCHA handling. It validates HTTP(S) URLs,
resolves each host before and after every request, rejects any non-global
address, manually validates every redirect, prevents repeated URLs, and bounds
depth, redirects, logical requests, timeout, content type, and accepted body
size. Responses over the limit are rejected and never persisted. Because the
installed Scrapling session buffers a response before exposing it, the body
limit bounds accepted/processed data but cannot stop a dishonest server at the
byte boundary; deployment-level egress and response limits remain recommended.

## UI and execution

Source Discovery is an onboarding/recovery workflow, not the vacancy update
transport. Once an approved active CompanySource exists, **Update jobs** and
**Update all** dispatch that row directly through its registered ATS adapter;
they do not import DiscoveryService or initialize Tavily. Candidates and the
connected CompanySource remain stored between updates. Rediscovery occurs only
for a company without a source, through the explicit **Discover again** action,
or through a separate operator-confirmed recovery procedure after a persistent
source fault. One failed adapter run is recorded normally and never triggers
automatic search.

Company detail exposes one **Manage sources** dialog with exactly two tabs.
**Connected** owns manual CompanySource management; **Discovered** owns the
optional official-domain field, **Discover sources**, current progress/result,
all candidates from the latest run, evidence, redirects, adapter/investigation
task drafts, revalidation, and manual connection. Discovery is no longer an
always-visible page section. Candidate cards derive their presentation state
from saved DiscoveryCandidate data, the current registry and URL validation;
the UI does not introduce another source persistence model. Every run snapshots
all existing CompanySource rows plus retained non-rejected source candidates,
then merges current crawl/search results by platform and adapter tenant
identity. Candidate cards label their origin. `DiscoveryAdapterCheck` records
one result per registered platform (`found`, `already_connected`, `not_found`,
`not_checked`, `search_failed`, or `validation_failed`), and the UI reports
checked/registered, sources found, already connected, not found, and not checked.
A budget or provider interruption is labeled **Partial discovery**; it is never
described as complete coverage. Previous valid candidates do not disappear on
an explicit repeat run.

Single and bounded bulk connection endpoints re-scope candidates to the
Company and latest run and repeat registry/URL validation before writing.
Equivalent sources are linked without changing their existing approval or
active state, and blocked/rejected rows are never re-approved. **Revalidate**
uses only the saved candidate URL and current adapter registry: it performs no
search and never creates a CompanySource. Adapter/investigation task drafts are
built solely from stored run/candidate evidence and explicitly label missing
fields as `Not discovered`.

Work runs on the project's shared bounded executor with a
company-keyed discovery guard, so the HTTP request is not blocked and duplicate
in-process runs are rejected. The executor is intentionally process-local; a multi-process
deployment needs a shared queue/lock before increasing web-worker count.

Automated discovery tests are fully offline. A corrective opt-in live diagnostic
on 2026-08-13 used the real background path, Scrapling 0.4.8, a temporary SQLite
database, and a manually supplied `datasentics.com` domain. Three ordinary GETs
returned HTTP 200 for the official homepage, careers homepage, and `/jobs` page.
The run finished `connected` with one approved/active DreamJobs source at
`https://careers.datasentics.com/jobs`; the temporary database was removed.

A subsequent Tavily production diagnostic on the same date used the real Django
view/background path, no manual domain, no Brave key, no Tavily key, and an
automatically removed SQLite database. Data Sentics used two keyless Tavily
POSTs and three crawler GETs, finished `connected`, and created one approved,
active DreamJobs source. The sequential repeat used the same five bounded
operations, finished `already_connected`, and preserved that single source.
Siemens used two keyless Tavily POSTs and eight redirect/page GETs, produced
multiple credible corporate identities and careers links, and correctly
finished `needs_review` with no selected official URL and no source. Every Tavily request carried the
keyless header and no Authorization header; no run contained `No active session
available`.

The Acuity Analytics blocked-site inventory diagnostic on 2026-08-13 used the
real `ControlledBackgroundExecutor`, production Tavily/Scrapling transports,
keyless diagnostic mode, and an automatically removed database seeded with
copies of its two existing approved/active sources. Tavily selected
`https://www.acuityanalytics.com/`; one ordinary crawler GET returned HTTP 403.
Discovery retained that official candidate. Its two fallback queries found
`https://ascent.applytojob.com/apply`, whose company-bearing metadata and hosted
URL produced the registered JazzHR detection. Because Darwinbox and JazzHR were
already inventoried, only missing DreamJobs and Lever received adapter-specific
searches. The run used five Tavily POSTs plus one crawler GET and finished
`already_connected`. Coverage was 4/4: Darwinbox and JazzHR
`already_connected`, DreamJobs and Lever `not_found`, none `not_checked`.
Exactly the original two CompanySource rows remained, with both URLs and their
approval/active states unchanged. No bypass, retry, proxy, browser, cookie, or
stealth behavior was used.

Scrapling 0.4.8 interprets `retries` as the total number of attempts inside
`_SyncSessionLogic._make_request`. Therefore the no-extra-retry configuration is
`retries=1`; `retries=0` performs no request and misleadingly raises
`No active session available.` Any future live diagnostic must remain explicitly
opt-in, public-only, and bounded.

## Manual verification

Start Django with the normal settings, process-local background executor, and
`SOURCE_DISCOVERY_TAVILY_API_KEY` set. Leave the Brave key unset. Create or
select a company named `Data
Sentics`, open **Manage sources → Discovered**, leave **Official domain** blank,
press **Discover sources**, and wait
for the run to finish. Inspect the latest `DiscoveryRun`, its candidates, and
the related `CompanySource`; the expected public chain is
`datasentics.com -> careers.datasentics.com/jobs -> dreamjobs -> connected` (or
`already_connected` only after the explicit **Discover again** action). Repeat
for `Siemens` using only its name. Normal search requests must use Tavily Bearer
authorization; a source may be
connected only if a registered ATS passes adapter validation, otherwise the
honest terminal state is `needs_review`, `unsupported`, or `not_found` with the
discovered evidence. Then run **Update jobs** repeatedly and verify that only
the stored ATS adapter runs and no new DiscoveryRun or Tavily request appears.
Use a temporary SQLite database for diagnostics, never the working `db.sqlite3`.
For an intentionally keyless diagnostic only, unset the key and explicitly set
`SOURCE_DISCOVERY_TAVILY_KEYLESS_DIAGNOSTIC=true`.

Official Tavily references: [keyless access](https://docs.tavily.com/documentation/keyless),
[Search endpoint](https://docs.tavily.com/documentation/api-reference/endpoint/search),
and [credits and limits](https://docs.tavily.com/documentation/api-credits).
