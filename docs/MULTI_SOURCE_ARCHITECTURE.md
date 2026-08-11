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

The schema, source ownership, source-scoped persistence/reconciliation, and Company multi-source orchestration are implemented. Source-management UI and additional ATS adapters are not implemented.

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

## Implemented

- CompanySource schema foundation and deterministic legacy backfill;
- CompanySource ownership for JobPosting and ScrapeRun;
- source-local identity, persistence, and reconciliation;
- source-level lifecycle and duplicate protection;
- `submit_source` and `submit_company`;
- Company multi-source orchestration and failure isolation;
- transactional aggregate Company status/time;
- multi-source-aware polling, Update jobs, and Update all;
- existing single-source Olo/Lever compatibility.

## Transitional legacy fields

The staged migration still retains:

- `Company.source` and `Company.source_jobs_url`;
- `JobPosting.company` and `JobPosting.source`;
- `ScrapeRun.company`;
- nullable source-ownership fields for historical compatibility.

Legacy exactly-one-source resolution remains only for compatibility entry points and tests. Normal Company UI execution uses `submit_company` and does not use legacy fields to choose an execution source.

## Not implemented

- source-management UI or Add/Edit/Disable Source workflows;
- automatic Source Discovery or LinkedIn-to-careers discovery;
- `DarwinboxSourceAdapter`, `JazzHRSourceAdapter`, Greenhouse/Ashby adapters, or `LinkedInSourceAdapter`;
- cross-source vacancy matching/deduplication or `CanonicalVacancy`;
- final removal of legacy Company fields or final non-null ownership cleanup;
- a parent/group ScrapeRun.

## Acuity reference case

The architecture can now represent and orchestrate two independent sources for Acuity. This is architecture capability, not an active integration. Darwinbox and JazzHR adapters are not implemented, Acuity production monitoring is not enabled, and Source Discovery remains manual/outside the application. No vacancy-completeness claim is made.

## Conceptual onboarding

For a future Company, identify the official jobs source and its ATS. If a production adapter already exists, create a reviewed CompanySource using that platform key and URL. If the platform is new, implement and approve one shared SourceAdapter, then reuse it for other companies on that ATS. These are architecture steps, not claims about currently available management UI.

## Source Discovery status

Source Discovery remains a proposed future enhancement: locate an official
careers site from a Company or LinkedIn reference, identify its ATS, and create
a CompanySource candidate for review. That investigation currently happens
manually outside the application. A LinkedIn reference is not executable
without a separately approved and implemented adapter/access path.

## Next staged slice

Slices 1-3 are complete. The next safe step is Slice 4: source-management UI. Source Discovery and additional ATS integrations remain separate future work.
