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

The shared pipeline supports multiple source roles through the same
source-owned execution contract:

- `lever`: registered, user-selectable, executable production adapter for
  public Lever career sources.
- `darwinbox`: registered, user-selectable, executable adapter implementing the
  observed public Darwinbox contract through a normal headful system-Chrome
  session.
- `jazzhr`: registered, user-selectable, executable plain-HTTP adapter for
  validated public `<tenant>.applytojob.com/apply` sources.
- `dreamjobs`: registered, user-selectable, executable plain-HTTP adapter for
  structurally verified public DreamJobs `/jobs` pages.
- `zoho_recruit`: registered, user-selectable, executable plain-HTTP adapter for
  validated public Zoho Recruit career sites using the embedded published jobs
  snapshot.
- `generic`: registered executable fallback for eligible public careers pages
  that do not require a dedicated ATS adapter. It is connected through the
  bounded discovery/auto-detection path rather than exposed as a manual
  platform choice.
- `fixture`: registered internal/test adapter. It reads local synthetic data,
  reports `requests_made=0`, and is excluded from user-managed source creation.

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
already-running, skipped, and failed source IDs. Every approved, active,
executable CompanySource is independently eligible for **Update jobs** and
**Update all**. Company orchestration does not use legacy Company fields to
select one source.

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
separately from Company fields through one responsive Manage sources dialog.
Its Connected/Discovered tabs combine manual source management with the latest
Discovery run and candidate actions without nested dialogs. Tests cover the
compact empty/summary states, multiple source rows, URL-only auto-detected Add
source, bounded custom-domain signature detection, duplicate handling, immutable source
provenance, company scoping, POST-only activation changes, and RUNNING-source
edit/deactivation protection.

Source creation accepts one public Jobs URL, reuses Discovery detectors and
Generic eligibility, and creates an approved active CompanySource only after a
fail-closed classification. Fixture remains available to internal pipeline tests
but cannot be assigned through this UI. Existing internal or unsupported rows
remain visible without being exposed through auto-detection.

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

## DreamJobs complete-snapshot integration proof

The adapter first verifies a configured custom domain from its server-rendered
Next.js data and DreamJobs static assets. It then consumes the public GraphQL
listing/detail contract used by that page. Offline tests cover Data Sentics-
shaped embedded results, strict platform detection, URL canonicalization,
stable IDs and canonical detail URLs, missing optional fields, HTML description
cleanup, empty snapshots, pagination, repeated-page termination, page/request
limits, GraphQL and detail failure, request accounting, registry/UI exposure,
and a source-owned pipeline run. The integration test also repeats the same
snapshot and proves no duplicate `JobPosting` rows are created and an unrelated
source is untouched.

The bounded public audit observed Data Sentics SSR listing data and successfully
queried one public detail through the page's own GraphQL contract. A later
bounded production-adapter run returned all six advertised jobs in seven HTTP
operations with non-empty descriptions. The same adapter then ran through the
source-owned pipeline against an automatically removed temporary SQLite
database: SUCCESS, Found 6, Created 6, Failed 0, Requests 7, Persisted 6. The
working `db.sqlite3` was not opened. Automated pytest remains completely offline.

The DreamJobs closeout quality gate passed with 29 focused tests and 713 total
tests (172 existing third-party `lxml` deprecation warnings). Ruff, MyPy strict
over 36 source files, Django system check, migration dry-run, dependency check,
and diff check passed.

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

## 2026-08-22 closeout verification

The final closeout verifies the current multi-source application rather than
replacing the historical adapter-specific baselines recorded above.

Current executable production paths include Lever, Darwinbox, JazzHR,
DreamJobs, Zoho Recruit, and Generic. Fixture remains internal/test-only, and
LinkedIn remains outside production scope.

The closeout coverage includes:

- source-owned execution, persistence, reconciliation, and ScrapeRun lifecycle;
- multi-source Company orchestration and failure isolation;
- source-management and Source Discovery workflows;
- Zoho Recruit adapter integration;
- Generic listing and detail extraction;
- Generic semantic/`JobPosting` JSON-LD metadata extraction;
- Generic WordPress/Elementor description fallback;
- explicit labelled HR metadata extraction without ambiguous prose inference;
- shared persistence of `employment_type`, `seniority_level`, and
  `compensation_text`;
- current Company and global vacancy-table HR columns;
- bounded Generic detail enrichment with a maximum of 50 detail pages per
  source run and preservation of previously persisted metadata.

Automated tests remain offline and use temporary/fake source data rather than
live job-board requests. Bounded manual public-page checks were used only as
technical evidence for real-world Generic structures; they are not part of the
automated regression suite and do not introduce company-specific parser rules.

Final regression result on 2026-08-22:

- **1023 passed**;
- **184 warnings**, from the existing third-party `lxml` deprecation warning
  set;
- `git diff --check`: clean.

These final totals supersede earlier totals only as the current full-suite
baseline. Earlier numbers in this document remain valid historical
adapter/milestone verification records.
