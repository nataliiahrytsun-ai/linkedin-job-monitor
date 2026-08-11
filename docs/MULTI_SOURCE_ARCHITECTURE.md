# Multi-source architecture

## Purpose and current status

The application monitors organisations, but an organisation can publish jobs
through more than one independent careers feed. `CompanySource` provides the
ownership boundary needed to keep those feeds separate:

```text
Company
├── CompanySource
├── CompanySource
└── ...
```

- `Company` is the monitored organisation.
- `CompanySource` is one concrete careers site or job feed for that
  organisation. It stores the platform/source key, `source_jobs_url`, approval
  status, and active state.
- `SourceAdapter` is the fetching and mapping implementation for one ATS or
  platform.

The schema and source-scoped backend ownership are implemented. Execution of
all sources for one Company and source-management UI are not implemented yet.

## CompanySource is not an adapter

An adapter is not created separately for every Company. One adapter supports
many source configurations on the same platform. For example:

```text
LeverSourceAdapter
├── Olo      -> CompanySource(url=https://jobs.lever.co/olo)
├── Company B -> CompanySource(url=https://jobs.lever.co/company-b)
└── Company C -> CompanySource(url=https://jobs.lever.co/company-c)
```

Each organisation owns its own `CompanySource`, while all three configurations
use the single registered `LeverSourceAdapter`. The same architectural rule is
intended for future Darwinbox, JazzHR, Greenhouse, or other ATS integrations.
Those adapters are not implied to exist merely because the model can represent
their sources.

## Implemented behavior

### Ownership foundation

- The `CompanySource` model exists.
- Legacy Company source configurations were deterministically backfilled into
  generated CompanySource rows.
- `JobPosting.company_source` and `ScrapeRun.company_source` provide the source
  ownership context while remaining nullable for staged migration compatibility.

### Pipeline and registry

Production source execution uses an explicit `CompanySource` context. The
adapter registry selects the implementation through `CompanySource.source`,
and the adapter receives that source's `source_jobs_url`.

### Persistence and identity

Production persistence creates and updates a posting inside one
`CompanySource`. Identity is source-local:

```text
(company_source, source_job_id)
```

with the existing canonical URL/dedupe-key fallback also scoped to that source.
The same external ID, title, location, or URL in another CompanySource is not a
cross-source match.

### Reconciliation

A successful snapshot reconciles only postings owned by the executed
CompanySource. Seen posting IDs are validated against that same source and an
ID from another source is rejected rather than silently ignored.

```text
Company Acuity
├── Source A -> posting A
└── Source B -> posting B

Empty SUCCESS snapshot for Source A:
posting A -> may accumulate a miss and become not_found
posting B -> unchanged
```

This isolation prevents one incomplete or empty feed from deactivating jobs
owned by another feed of the same Company.

### ScrapeRun

Every new production pipeline run is associated with the executed
`CompanySource`. The transitional `ScrapeRun.company` field remains consistent
with `ScrapeRun.company_source.company`.

Company-level active-job counters currently aggregate active source postings.
They do not represent cross-source deduplicated vacancies.

## Transitional legacy execution

The staged migration intentionally retains these fields:

- `Company.source`
- `Company.source_jobs_url`
- `JobPosting.company`
- `JobPosting.source`
- `ScrapeRun.company`

They remain for backward compatibility, provenance, and the existing UI and
background entry points. The legacy Company execution path resolves exactly
one approved, active CompanySource compatible with the legacy configuration.
It fails closed when there are zero or more than one executable sources. It
does not use `.first()` or select an arbitrary source.

This preserves the existing single-source Olo/Lever flow without claiming that
Company-wide multi-source orchestration is complete.

## Not implemented

The following are not implemented at the current Slice 2 boundary:

- running all active sources of a Company from one action;
- source-level background orchestration;
- parallel or multi-source execution orchestration;
- multi-source/source-management UI;
- automatic Source Discovery;
- automatic LinkedIn-to-careers-source discovery;
- `DarwinboxSourceAdapter`;
- `JazzHRSourceAdapter`;
- `LinkedInSourceAdapter`;
- cross-source vacancy deduplication;
- a `CanonicalVacancy` abstraction.

## Acuity reference case

The Acuity source audit identified a possible architecture:

```text
Acuity Analytics
├── Darwinbox
└── JazzHR
```

This audited case motivated multi-source ownership because one Company can have
independent feeds. It is not an active production integration: Darwinbox and
JazzHR adapters have not been implemented, Acuity multi-source monitoring is
not enabled, and no completeness claim is made.

## Conceptual onboarding for a future source

When investigating a new Company:

1. Identify its official public careers/jobs source.
2. Identify the ATS or platform behind that source.
3. Check whether a production adapter for that platform is registered.
4. If it exists, create a CompanySource with that platform key and source URL.
5. If the platform is new, implement and approve one SourceAdapter for the
   platform.
6. Reuse that adapter for other companies on the same ATS through their own
   CompanySource rows.

For Company X on Lever, the existing `LeverSourceAdapter` is reused and no new
adapter is needed. For Company Y on a future Greenhouse source, a
`GreenhouseSourceAdapter` would first be required if none exists. These are
architecture steps, not claims about currently available admin or UI workflows.

## Source Discovery

Source Discovery is a proposed future enhancement:

```text
Company / LinkedIn reference
-> find official Careers site
-> identify ATS
-> create a CompanySource candidate for review
```

Discovery currently happens outside the application through manual
investigation. A LinkedIn reference is not an executable source unless a
separately approved and implemented LinkedIn adapter/access path exists.

## Next staged slice

The next safe architecture step is source-level lifecycle/background execution
and Company multi-source orchestration. This document does not describe that
Slice 3 work as implemented.
