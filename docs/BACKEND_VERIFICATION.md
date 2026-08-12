# Backend and quality verification

## Verified production flow

The current source-owned path is:

```text
Company / UI action -> submit_company -> eligible CompanySources
                    -> source-level background execution
                    -> immutable source registry -> SourceAdapter -> SourceBatch
                    -> normalization -> source-scoped persistence/reconciliation
                    -> one source-owned ScrapeRun per execution
                    -> aggregate Company state and multi-source-aware polling
```

`CompanySource` is the execution and ownership boundary. Tests cover independent
RUNNING runs for two sources of one Company, same-source duplicate rejection,
deterministic Company orchestration, failure isolation, source-scoped identity
and reconciliation, aggregate Company state, and the fast-source polling race.

The shared pipeline supports two deliberately different source roles:

- `lever`: current production and user-selectable adapter. An executable
  CompanySource stores `source="lever"` and a public URL such as
  `https://jobs.lever.co/olo`.
- `fixture`: registered internal/test adapter. It reads local synthetic data,
  reports `requests_made=0`, and is excluded from user-managed source creation.
- `darwinbox`: registered, user-selectable, executable adapter implementing the
  observed public Acuity Darwinbox contract through a normal headful system-
  Chrome session.
- `jazzhr`: registered, user-selectable, executable plain-HTTP adapter for
  validated public `<tenant>.applytojob.com/apply` snapshots.

LinkedIn has no production adapter or registry key and remains blocked pending
a separate feasibility/access/approval decision.
Direct automated HTTP listing access received Cloudflare 403, and headless
Chrome received a minimal non-bootstrapping document. A fresh plain headful
system-Chrome run rendered the public SPA and received the initial listing with
no login, cookies, challenge, or interaction. The implemented transport uses
that bounded public navigation model without stealth or API replay. This does
not claim that every Darwinbox installation is supported.

## Source-level orchestration proof

`submit_source(company_source)` owns one explicit source execution. Active
tasks are keyed by `company_source_id`. `submit_company(company)` evaluates all
CompanySource rows in deterministic order and independently reports submitted,
already-running, skipped, and failed source IDs. Both approved active Lever and
Darwinbox sources are eligible for Update jobs and Update all. It does not use legacy
Company fields to select one source.

The regression suite proves:

- two eligible sources create two independently owned ScrapeRun rows;
- one already-running source does not block another eligible source;
- inactive/unapproved sources are skipped and zero executable sources fail
  closed;
- one source may succeed while another fails without rollback or cross-source
  reconciliation;
- Company aggregate state is independent of callback order;
- polling requires a post-baseline terminal run for every submitted source.

## Source-management UI proof

Company create/edit now manages Company-level fields without mutating the
transitional legacy source fields. CompanySource configuration is managed
separately from Company detail through responsive dialogs. Tests cover the
compact empty/summary states, multiple source rows, registry-backed Add source,
server-side Lever URL validation, duplicate handling, immutable source
provenance, company scoping, POST-only activation changes, and RUNNING-source
edit/deactivation protection.

Manual source creation accepts only user-selectable registry adapters, currently
Lever, and creates an approved active CompanySource. Fixture remains available
to internal pipeline tests but cannot be assigned through this UI. Existing
internal or unsupported rows remain visible without being exposed as ordinary
user-manageable source options.

## Lever multi-page integration proof

`test_lever_multi_page_submission_persists_complete_snapshot_without_network`
in `tests/test_background.py` enters through the production background path.
The registry selects `LeverSourceAdapter`; injected fake HTTP returns two JSON
fixtures:

```text
request 1: limit=2, skip=0 -> lever-1, lever-2
request 2: limit=2, skip=2 -> lever-3
```

The test verifies two calls, three normalized/persisted jobs, reconciliation,
a terminal SUCCESS ScrapeRun, and `requests_made=2`. A separate Olo regression
proves that the single Lever CompanySource is submitted through
`submit_company`. No real network request is made.

## Darwinbox complete-snapshot integration proof

`DarwinboxSourceAdapter` opens the observed public UI route:

```text
/ms/candidatev2/<companyId>/careers/allJobs
```

The normal headful SPA emits the initial listing request; pagination clicks its
visible Load More control and passively captures each resulting response. One
`fetch()` walks page 1, page 2, and subsequent pages automatically. It
deduplicates on Darwinbox `id` and returns `SourceBatch` only after the unique
count reaches `job_counts`. Incomplete pagination raises `SourceError`; the
pipeline stores a FAILED ScrapeRun and performs no reconciliation. Listing
`jd` avoids detail work, while an empty `jd` triggers one matching public
candidate-v2 detail navigation. `requests_made` includes listing and detail data
operations but excludes browser assets/navigation.

Offline adapter tests cover one-, two-, and three-page completion, empty
complete snapshots, within/across-page duplicates, malformed or inconsistent
responses, detail fallback and ID validation, request accounting, page limits,
and transport failures. Background integration tests prove source-owned
persistence, SUCCESS reconciliation isolation, equal IDs in different
CompanySources, and preservation of existing jobs after incomplete fetch. No
real Darwinbox request is part of automated verification.

## Reproducible checks

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
git diff --check
```

Bootstrap and migration tests use unique temporary SQLite paths. The working
repository `db.sqlite3` is not opened, migrated, replaced, or removed for the
verification gate.

## Current verified Darwinbox headful transport baseline

- targeted source/UI/background tests: **184 passed**;
- full pytest: **540 passed**, with 150 existing third-party `lxml` deprecation
  warnings;
- Ruff: **All checks passed**;
- MyPy: **Success — 29 source files**;
- Django system check: **0 issues**;
- migration check: **No changes detected**;
- `pip check`: **No broken requirements found**;
- `git diff --check`: **Pass**.

These checks used temporary database configuration. The working `db.sqlite3`
was not opened or used, and no job-source network requests were performed.

Manual visual review covered the compact Company Sources summary, Manage
sources dialog, Add/Edit dialogs, desktop source rows, responsive mobile source
management, compact mobile Company information rows, and checked narrow widths
without observed horizontal overflow. This is a focused visual review, not an
automated browser/device certification.

## Scope boundary

This evidence verifies the Darwinbox adapter against offline representations of
the observed Acuity contract together with the existing source-neutral pipeline.
The headful transport implementation is offline-tested; no live request is part
of the automated gate. It does not prove all Darwinbox tenants, guarantee a
headless/server-only deployment, or complete Acuity multi-source monitoring. It
does not verify or claim Source Discovery, a LinkedIn adapter, company-wide
Acuity completeness, cross-source vacancy deduplication, hard source delete,
run cancellation, or final legacy-schema cleanup. Lever remains unchanged.

JazzHR closeout verified the configured interim Ascent/Acuity source directly:
23 unique opaque IDs produced 23 records in 24 requests (one listing and 23
details), using JSON-LD for 6 details and strict HTML fallback for 17. Manual
source creation and **Update jobs** succeeded; a repeat run reported Found 23,
Created 0, Updated 0, and Requests 24 without duplicate postings.
