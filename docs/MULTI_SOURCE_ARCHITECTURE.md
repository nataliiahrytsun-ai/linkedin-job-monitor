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
multi-source orchestration, and source-management UI are implemented. Lever is
the production-approved/user-selectable adapter. Darwinbox is implemented and
registered as executable, but remains hidden from normal source creation while
its production/policy approval is **INDETERMINATE**. Additional ATS adapters and
automatic Source Discovery are not implemented.

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
Sources summary opens the **Manage sources** dialog for the secondary source
configuration workflow. The dialog lists multiple sources independently and is
responsive on narrow viewports.

Manual **Add source** obtains its platform choices from the registry's
user-selectable API. Lever is currently the only production/user-selectable
adapter. A valid public jobs URL is required and checked server-side. A manually
created source starts as `approval_status=approved` and `is_active=True`.
Fixture remains registered for internal tests but is not offered or accepted as
a normal user-managed source. Darwinbox is registered as an executable adapter
but remains non-selectable because implementation does not itself grant
production/policy approval. JazzHR is not selectable because its adapter does
not exist.

The source platform is immutable after creation because CompanySource is both a
provenance and execution boundary. **Edit source** can update the supported
source configuration, including its URL, but cannot silently convert the row to
another platform. A different platform requires a new CompanySource.

Activate and Deactivate are separate POST-only actions. An unapproved or
unregistered source cannot be made executable. The conservative management
policy also blocks unsafe editing or deactivation while that source has a
RUNNING ScrapeRun. Hard deletion and run cancellation are not implemented.

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
- existing single-source Olo/Lever compatibility.

## Darwinbox adapter boundary

`DarwinboxSourceAdapter` implements the public transport contract observed on
the Acuity Darwinbox tenant. A listing request uses:

```text
POST /ms/candidateapi/job/alljobs?companyId=<companyId>
```

One `adapter.fetch()` automatically advances the page-based API until the
accumulated unique Darwinbox `id` values satisfy the response `job_counts`.
That `id` is the stable source-local job ID. A `SourceBatch` is returned only
for a complete snapshot. Empty, repeated, inconsistent, or page-limited
pagination before completion raises `SourceError`; the pipeline records a
FAILED source-owned ScrapeRun and does not reconcile jobs from a partial
snapshot.

Listing `jd` is used when present. The public detail GET is requested only as a
fallback when listing `jd` is empty. `requests_made` includes every listing and
detail request. The adapter uses the existing source-neutral persistence and
reconciliation path and does not change Lever behavior.

This is technical compatibility with the observed public Acuity Darwinbox
contract, not a claim that all Darwinbox installations are compatible. The
adapter is executable through the internal registry but is deliberately absent
from normal Add Source choices while production/policy approval remains
**INDETERMINATE**. Acuity production monitoring is not thereby complete or
approved.

## Transitional legacy fields

The staged migration still retains:

- `Company.source` and `Company.source_jobs_url`;
- `JobPosting.company` and `JobPosting.source`;
- `ScrapeRun.company`;
- nullable source-ownership fields for historical compatibility.

Legacy exactly-one-source resolution remains only for compatibility entry points and tests. Normal Company UI execution uses `submit_company` and does not use legacy fields to choose an execution source.

Normal Company create/edit forms no longer expose or mutate
`Company.source`/`Company.source_jobs_url`. These fields remain in the schema
only for staged migration and backward compatibility; final cleanup has not
been completed.

## Not implemented

- automatic Source Discovery or LinkedIn-to-careers discovery;
- `JazzHRSourceAdapter`, Greenhouse/Ashby adapters, or `LinkedInSourceAdapter`;
- cross-source vacancy matching/deduplication or `CanonicalVacancy`;
- final removal of legacy Company fields or final non-null ownership cleanup;
- hard deletion of CompanySource or run cancellation;
- a parent/group ScrapeRun.

## Acuity reference case

The architecture can represent and orchestrate two independent sources for
Acuity. Darwinbox transport compatibility is implemented against the observed
public Acuity contract, but the adapter is not production-approved or exposed
through normal source creation. JazzHR is not implemented, Acuity production
monitoring is not complete or enabled, and Source Discovery remains
manual/outside the application. No vacancy-completeness claim is made.

## Conceptual onboarding

For a future Company, identify the official jobs source and its ATS. If a
production/user-selectable adapter already exists, use the source-management UI
to create its CompanySource with the platform key and URL. If the platform is
new, implement, register, and approve one shared SourceAdapter before exposing
it to users, then reuse it for other companies on that ATS.

## Source Discovery status

Source Discovery remains a proposed future enhancement: locate an official
careers site from a Company or LinkedIn reference, identify its ATS, and create
a CompanySource candidate for review. That investigation currently happens
manually outside the application. A LinkedIn reference is not executable
without a separately approved and implemented adapter/access path.

## Next work

Slices 1-4 and the bounded Darwinbox adapter implementation are complete. A
separate production/policy decision and source-configuration review would be
required before Darwinbox could be exposed to users or enabled for Acuity.
JazzHR, Source Discovery, and other ATS integrations remain separate future
work.
