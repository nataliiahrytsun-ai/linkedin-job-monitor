# Multi-source architecture
## Purpose and current status

One monitored organisation can publish jobs through several independent careers feeds:

```text
Company
|-- CompanySource
|-- CompanySource
`-- ...
```

- `Company` is the monitored organisation.
- `CompanySource` is one concrete careers site or job feed. It stores the platform/source key, `source_jobs_url`, approval status, and active state.
- `SourceAdapter` is the fetching and mapping implementation for one ATS or platform. One registered adapter can serve many CompanySource rows.

The schema, source ownership, source-scoped persistence/reconciliation, Company
multi-source orchestration, and source-management UI are implemented. Lever,
Darwinbox, JazzHR, DreamJobs, and Zoho Recruit are production-approved/user-selectable
adapters. Generic is an executable discovery/auto-detected fallback for eligible
public careers pages and is intentionally not part of the manual platform dropdown.
Darwinbox executes through its verified normal headful-browser transport;
JazzHR uses ordinary server-rendered HTTP. Production Source Discovery is a separate orchestration layer. Generic provides a
conservative fallback for eligible public careers pages that do not map to a
registered dedicated ATS adapter.

## CompanySource is not an adapter

An adapter is not created separately for every Company:

```text
LeverSourceAdapter
|-- Olo       -> CompanySource(url=https://jobs.lever.co/olo)
|-- Company B -> CompanySource(url=https://jobs.lever.co/company-b)
`-- Company C -> CompanySource(url=https://jobs.lever.co/company-c)
```

Each organisation owns its own CompanySource configuration, while all three use the single registered `LeverSourceAdapter`. The same design can support future ATS integrations, but an adapter is not considered supported until it has actually been implemented, registered, and tested.

## Execution ownership

`CompanySource` is the execution boundary:

```text
Company
|-- CompanySource A
|   `-- ScrapeRun A
`-- CompanySource B
    `-- ScrapeRun B
```

Each source is submitted independently, receives its own `ScrapeRun`, owns its request and job counters, and runs persistence and reconciliation only for its own postings. There is no parent or group ScrapeRun.

The database and in-process executor enforce source-level duplicate protection. Two sources of one Company may run concurrently:

```text
Acuity / Source A -> RUNNING
Acuity / Source B -> RUNNING
```

Two simultaneous runs for the same source are rejected:

```text
Acuity / Source A -> RUNNING #1
Acuity / Source A -> RUNNING #2  (rejected)
```

Legacy ScrapeRun rows without `company_source` retain transitional Company-level RUNNING protection.

## Background orchestration

`ControlledBackgroundExecutor.submit_source(company_source)` validates and submits one explicit CompanySource. Active-task ownership is keyed by `company_source_id`, so work for Source A does not block Source B.

`ControlledBackgroundExecutor.submit_company(company)`:

1. verifies that the Company is active;
2. reads all CompanySource rows in deterministic primary-key order;
3. independently submits each eligible source;
4. reports submitted, already-running, skipped, and failed source IDs.

It does not call `.first()`, select an arbitrary source, or fall back to `Company.source`. A source is executable when the Company is active, the CompanySource is active and approved, its adapter is registered/executable, and its required configuration is accepted by the adapter path. Inactive, unapproved, or unregistered sources are not run. A Company with no executable source fails closed.

## Persistence, identity, and reconciliation

Production persistence creates and updates postings inside one CompanySource. Identity is source-local:

```text
(company_source, source_job_id)
```

The canonical URL/dedupe-key fallback is also scoped to the source. An equal external ID in another CompanySource is not a duplicate.

A successful snapshot reconciles only postings owned by its executed source:

```text
Company Acuity
|-- Source A -> posting A
`-- Source B -> posting B

Empty SUCCESS snapshot for Source A:
posting A -> may accumulate a miss and become not_found
posting B -> unchanged
```

## Failure isolation

Company orchestration is not one atomic transaction:

```text
Company
|-- Source A -> SUCCESS
`-- Source B -> FAILED
```

Source A keeps its successful persistence and reconciliation. Source B keeps its own FAILED ScrapeRun. Its failure neither rolls back Source A nor reconciles Source A jobs. Company-level state is recomputed from both relevant source results.

## Company aggregate state

`Company.last_scrape_status` and `Company.last_scraped_at` remain transitional aggregate/cache fields. For active, approved CompanySources, the latest run of each source is evaluated in this order:

1. any `running` -> Company `running`;
2. otherwise any `failed` -> Company `failed`;
3. otherwise any `partial` -> Company `partial`;
4. otherwise, if every relevant source has a latest `success` -> Company `success`;
5. otherwise -> existing `never` semantics.

`last_scraped_at` is the maximum relevant terminal `finished_at`, not the timestamp from whichever worker callback happened last. Lifecycle transitions recompute these fields transactionally while locking the Company row.

## Polling and Company actions

### Update jobs

Company-level **Update jobs** calls `submit_company(company)`. One executable source produces one execution; two executable sources produce two independent executions. The existing Olo workflow remains unchanged from the user's perspective:

```text
Olo -> Lever CompanySource -> submit_company(Olo) -> LeverSourceAdapter
    -> persistence -> reconciliation -> terminal ScrapeRun
```

This daily execution boundary never calls Source Discovery, Tavily, or another
SearchProvider. Thirty updates produce thirty adapter executions and zero
search requests. Discovery is initial source onboarding: it runs for a company
without a source, on the explicit **Discover again** action, or in a separate
operator-confirmed recovery workflow after a persistent source fault. A single
adapter failure never triggers automatic rediscovery.

### Polling

The submission carries the expected submitted CompanySource IDs and a pre-submission ScrapeRun baseline. The existing read-only status polling waits for a post-baseline run for every expected source and treats the submission as complete only when every such run exists and is no longer `running`.

If A is already `success` but B has not created its run row yet, polling continues. This handles very fast sources without treating the first terminal run as completion of the whole Company submission. Polling uses a five-second interval.

### Update all and Dashboard

**Update all** calls Company orchestration for each eligible active Company. An already-running Source A does not block submission of eligible Source B.

Dashboard **Running now** counts actual ScrapeRun rows with status `running`, not distinct companies. Two running sources of one Company therefore produce `Running now = 2`.

## Source-management UI

Company create/edit manages Company-level data only. Source-specific
configuration is managed separately as CompanySource data. Company detail keeps
the primary Company state, **Update jobs**, and saved jobs prominent; a compact
Sources summary opens one **Manage sources** dialog for the secondary source
workflow. It has exactly two tabs: **Connected** lists and edits CompanySource
rows, while **Discovered** starts Source Discovery and renders every candidate
from the latest run. Add/Edit forms are inline in the same dialog rather than
nested dialogs. Candidate connection is server-revalidated and company-scoped;
equivalent CompanySource approval/active state is preserved. The dialog is
responsive on narrow viewports.

**Add source** accepts one public Jobs URL and detects the platform server-side.
Hosted Lever, Darwinbox, JazzHR, and Zoho Recruit routes use the existing URL
detectors without an HTTP request. Custom-domain detection performs one bounded,
depth-zero Scrapling crawl and reuses the existing HTML-signature detectors for
DreamJobs and Zoho Recruit. A non-ATS page becomes Generic only when the existing
production eligibility gate accepts it and deterministic Generic extraction finds
at least one job link. Unknown, ambiguous, inaccessible, private, and internal
sources fail closed. Canonical source identity prevents an equivalent source from
being added twice to one Company. The source starts as
`approval_status=approved` and `is_active=True`.

The registry's user-selectable API remains the explicit/manual platform catalog;
auto-detection is a separate concern and does not add Generic or Fixture to that
catalog. Fixture remains registered and executable for internal tests only.

The source platform is immutable after creation because CompanySource is both a
provenance and execution boundary. **Edit source** can update the supported
source configuration, including its URL, but cannot silently convert the row to
another platform. A different platform requires a new CompanySource.

Disconnect and Reconnect are separate POST-only actions. An unapproved or
unregistered source cannot be made executable. The conservative management
policy also blocks unsafe editing, deactivation, or deletion while that source
has a RUNNING ScrapeRun. Run cancellation is not implemented.

A Company may exist with no CompanySource. The UI exposes the configuration
action, while **Update jobs** still fails closed when there is no executable
source. It does not fall back to legacy Company source fields.

## Implemented

- CompanySource schema foundation and deterministic legacy backfill;
- CompanySource ownership for JobPosting and ScrapeRun;
- source-local identity, persistence, and reconciliation;
- source-level lifecycle and duplicate protection;
- `submit_source` and `submit_company`;
- Company multi-source orchestration and failure isolation;
- transactional aggregate Company status/time;
- multi-source-aware polling, Update jobs, and Update all;
- responsive CompanySource management UI with Add, Edit, Activate, and
  Deactivate workflows;
- public DreamJobs custom-domain adapter using the verified Next.js/GraphQL
  contract and the existing source-owned pipeline;
- public Zoho Recruit adapter for the verified embedded career-site contract;
- Generic executable fallback for eligible public careers pages, including
  bounded progressive detail enrichment and conservative HR metadata extraction;
- production Source Discovery and Generic connection/auto-detection workflow;
- existing single-source Olo/Lever compatibility.

## Generic adapter boundary

`GenericSourceAdapter` is the executable fallback for eligible public careers
pages that do not map to a dedicated supported ATS adapter. It is deliberately
platform-neutral and reusable: a separate adapter is not created for each
Company.

Generic first performs deterministic listing extraction and validates that the
configured public page yields usable vacancy candidates. Detail pages are then
used for optional enrichment.

Detail extraction prefers explicit, reusable structures:

- `JobPosting` JSON-LD and semantic HTML;
- explicit description/content containers;
- common WordPress/Elementor content widgets when no semantic description is
  available;
- explicitly labelled HR metadata in structures such as definition lists,
  tables, labelled inline elements, and `Label: Value` text.

Current optional detail fields include description, location, country,
publication date, workplace type, employment type, seniority, and
`compensation_text`.

Generic is conservative. It does not infer HR metadata from ambiguous prose,
navigation, footer addresses, or experience wording. If a trustworthy value
cannot be identified, the field remains null.

### Bounded progressive detail enrichment

Generic detail fetching is bounded to **50 detail pages per source run**. This
limit is separate from listing requests and listing completeness.

For a source with more than 50 vacancies, the listing may be discovered in one
run while only a bounded subset receives detail enrichment. Later successful
**Update jobs** runs preserve previously stored detail metadata and can continue
enriching detail URLs that have not yet satisfied the enrichment criteria.

For a source with 50 or fewer discovered vacancies, all discovered detail pages
should normally be attempted within one source run.

Progressive enrichment does not guarantee that every optional field will
eventually become non-null. A missing field after a detail page has been
processed is not automatically an error: the public source may not publish that
value, or may not expose it in a structure Generic can identify safely.

Progressive enrichment changes metadata completeness only. Job identity,
source ownership, persistence, reconciliation, and ScrapeRun ownership remain
source-scoped and continue through the shared pipeline.

## Darwinbox adapter boundary

`DarwinboxSourceAdapter` implements the public transport contract observed on
the Acuity Darwinbox tenant. Its production transport opens:

```text
/ms/candidatev2/<companyId>/careers/allJobs
```

in a temporary normal headful system-Chrome session through Scrapling
`DynamicFetcher`. The SPA automatically issues the initial
`/ms/candidateapi/job/alljobs` request. The transport captures that response
passively and uses the visible **Load More** control for subsequent pages; it
does not replay the API directly. One `adapter.fetch()` advances until the
accumulated unique Darwinbox `id` values satisfy the response `job_counts`.
That `id` is the stable source-local job ID. A `SourceBatch` is returned only
for a complete snapshot. Empty, repeated, inconsistent, or page-limited
pagination before completion raises `SourceError`; the pipeline records a
FAILED source-owned ScrapeRun and does not reconcile jobs from a partial
snapshot.

Listing `jd` is used when present. A public candidate-v2 detail page is opened
only as a fallback when listing `jd` is empty, and its normal data response is
captured. `requests_made` counts listing and detail data operations, not browser
assets or document navigation. The adapter uses the existing source-neutral persistence and
reconciliation path and does not change Lever behavior.

The transport uses `headless=False`, system Chrome, `google_search=False`, no
resource blocking, one attempt, and no imported profile, cookies, login,
proxies, custom headers/user agent, fingerprint overrides, stealth, or CAPTCHA
interaction. Headless Chrome was observed to receive a minimal non-bootstrapping
document, while a fresh ordinary headful Chrome session rendered the public SPA
and received the initial listing successfully. System Chrome and an interactive
desktop are therefore deployment prerequisites. Failure to launch, render, or
capture raises `SourceError` and cannot reconcile a partial snapshot. This
verifies the observed Acuity contract, not every Darwinbox installation or
vacancy completeness.

## Transitional legacy fields

The staged migration still retains:

- `Company.source` and `Company.source_jobs_url`;
- `JobPosting.company` and `JobPosting.source`;
- `ScrapeRun.company`;
- nullable source-ownership fields for historical compatibility.

Legacy exactly-one-source resolution remains only for compatibility entry points and tests. Normal Company UI execution uses `submit_company` and does not use legacy fields to choose an execution source.

## JazzHR adapter boundary

`JazzHRSourceAdapter` accepts the public `/apply` and `/apply/jobs` listing
variants on a single `<tenant>.applytojob.com` host and canonicalizes listing
retrieval to `/apply`. It uses one ordinary Scrapling `FetcherSession`, with no
browser, login, cookies, stealth headers, impersonation, proxies, or retries
beyond the single configured attempt. The current audit found a server-rendered
complete listing with no pagination control. If a future listing exposes a next
page or offset control, the adapter fails closed rather than returning an
untraversed snapshot.

Current `/apply/<opaque-id>/<slug>` and legacy
`/apply/jobs/details/<opaque-id>` URLs normalize to the same opaque
`source_job_id`. Visible `Ref` values are deliberately ignored: the audited
listing contained one duplicated Ref across distinct opaque IDs. Every unique
listing link requires one successful public detail request. An unambiguous
`JobPosting` JSON-LD object is preferred. When no JobPosting candidate exists,
the offline-tested fallback requires a JazzHR `.job-header`, one matching `h2`,
structured `.job-attributes-container`, and a non-empty `#job-description`
outside any application form. It never reads the body or application form as a
description. Ambiguous or conflicting JSON-LD fails without fallback.
`requests_made` counts the listing attempt plus every required detail attempt.
Any transport, structural, identity, detail, conflict, challenge, or pagination
failure raises `SourceError`; no partial batch can reconcile jobs.

The pre-implementation audit observed 23 jobs and HTTP 200 for `/apply`,
`/apply/jobs`, and a sample detail. A later bounded run reached an HTTP-200
detail whose only JSON-LD object was `Organization`. A single targeted DOM
diagnostic confirmed a complete 4,413-character `#job-description` outside the
separate application form, so a strict HTML fallback was implemented and
verified offline. The final bounded live adapter run returned 23 records for 23
unique opaque IDs in 24 requests: 6 details used JSON-LD and 17 used HTML
fallback. The listing exposed 23 Ref values but only 22 unique values, further
confirming that Ref is not identity. Manual **Manage sources**/**Update jobs**
verification succeeded, and a repeat run created or updated no postings.

Normal Company create/edit forms no longer expose or mutate
`Company.source`/`Company.source_jobs_url`. These fields remain in the schema
only for staged migration and backward compatibility; final cleanup has not
been completed.

## DreamJobs adapter boundary

`DreamJobsSourceAdapter` accepts a public HTTPS `/jobs` URL on a custom domain.
It canonicalizes away UI-only `activeOpportunityId`, `page`, and
`similarVacancyId` parameters. The first ordinary Scrapling HTTP GET must expose
the verified current platform topology: Next.js `/jobs` data, a tenant
`clientName`, successful dehydrated client-configuration and opportunities-list
queries, and a DreamJobs static asset. This makes custom-domain support
structural rather than a hostname allowlist.

The embedded first page and advertised total seed the snapshot. Further pages,
when required, use the public unauthenticated GraphQL request made by the career
page; every stable opportunity ID then receives one public detail query.
Pagination and total consistency, unique IDs, page/request limits, timeout,
and bounded retries are enforced. Any missing detail or incomplete snapshot
fails the source run before persistence/reconciliation. A verified total of
zero is a successful complete snapshot. `source_job_id`, title, canonical job
URL, location/city/country, workplace type, employment type, and cleaned
description map into existing normalized fields. The source exposes salary, currency, and company name. Salary/compensation can now
map into the shared `compensation_text` field when the adapter exposes it. Employer
remains Company/CompanySource context rather than a duplicated JobPosting field, so no employer schema expansion
was introduced.

The 2026-08-13 audit verified this contract for Data Sentics at
`https://careers.datasentics.com/jobs`. It does not prove every DreamJobs site
or future frontend/API version. Source Discovery remains outside this adapter.

## Zoho Recruit adapter boundary

`ZohoRecruitSourceAdapter` uses one ordinary Scrapling HTTP GET for a public
HTTPS `/jobs/<page>` career site. It validates independent Zoho Recruit asset,
DOM, metadata, module, and jobs-layout signals before mapping the embedded
published jobs JSON. Zoho record IDs are stable source-local IDs; detail URLs
use the public `/jobs/<page>/<record-id>` route without a generated slug.

The public career-site display limit is 750 jobs. A snapshot at or above that
limit, visible pagination, empty or malformed payloads, conflicting metadata,
or missing platform signals fail closed without reconciliation. The adapter
does not use Zoho Recruit REST API/OAuth or any authenticated transport. See
[`docs/ZOHO_RECRUIT_SPIKE.md`](ZOHO_RECRUIT_SPIKE.md).

## Not implemented

- LinkedIn-derived discovery (name/domain discovery is implemented without LinkedIn);
- Greenhouse/Ashby adapters or `LinkedInSourceAdapter`;
- cross-source vacancy matching/deduplication or `CanonicalVacancy`;
- final removal of legacy Company fields or final non-null ownership cleanup;
- run cancellation;
- a parent/group ScrapeRun.

## Acuity reference case

The architecture can represent and orchestrate independent Darwinbox and
JazzHR sources for Acuity. Both are exposed through normal source creation.
The configured JazzHR adapter targets the interim Ascent/Acuity page only and
does not claim company-wide careers completeness. Source Discovery can retain
and present multiple candidates for review. Cross-source canonical deduplication is not
implemented.

## Source removal

Manage sources keeps Disconnect/Reconnect as the reversible monitoring control
and exposes a separate confirmed hard-delete action for user-owned connected
sources. This deletable set includes discovery-connected Generic sources as well
as manually selectable ATS sources, while internal adapters such as Fixture stay
excluded; Generic is not added to the manual Add source dropdown.
Deletion is rejected while that source has a RUNNING ScrapeRun. Otherwise one
atomic operation removes only JobPosting rows whose immutable `company_source`
points at the selected source, then removes the CompanySource. ScrapeRun and
Source Discovery history is retained with its `company_source` reference set to
NULL. Jobs owned by every other source remain untouched; equivalent jobs from
different sources remain independent because cross-source canonical matching is
not part of the current model.

## Conceptual onboarding

For a future Company, identify the official public jobs source and determine
whether it belongs to a supported ATS.

If a production/user-selectable adapter already exists, use the
source-management UI to create the corresponding CompanySource.

If the page is a public careers source without a supported ATS match, the
existing Generic eligibility and deterministic extraction checks should be
considered before implementing anything company-specific. An eligible source
can use the shared `generic` path.

Implement a new shared SourceAdapter only when the source requires a
platform-specific transport or contract that Generic cannot safely support.
That adapter should be registered and validated at platform level and then
reused for other companies on the same ATS; a separate adapter per Company is
not the intended architecture.

## Source Discovery status

Production Source Discovery now locates a probable official site from a company
name or supplied domain, performs a bounded safe careers crawl, detects the five
registered production ATS platforms, and either connects one high-confidence
source or persists review/unsupported candidates. It is deliberately separate
from adapters and reconciliation. A LinkedIn reference is not executable
without a separately approved and implemented adapter/access path. See
[`docs/SOURCE_DISCOVERY.md`](SOURCE_DISCOVERY.md).

## Next work

Slices 1-4 and the bounded Darwinbox headful-browser transport are complete.
Darwinbox is enabled for normal source creation and Company execution, subject
to its explicit interactive system-Chrome deployment requirement.
JazzHR, DreamJobs, Zoho Recruit, Generic, and the independent Source Discovery
layer are implemented. Additional dedicated ATS integrations remain separate
future work.
