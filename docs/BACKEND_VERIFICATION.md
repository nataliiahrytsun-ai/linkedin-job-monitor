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
  reports `requests_made=0`, and is excluded from Add Company.

LinkedIn has no production adapter or registry key. Darwinbox and JazzHR are
also not implemented. Their audit artifacts are not production support.

## Source-level orchestration proof

`submit_source(company_source)` owns one explicit source execution. Active
tasks are keyed by `company_source_id`. `submit_company(company)` evaluates all
CompanySource rows in deterministic order and independently reports submitted,
already-running, skipped, and failed source IDs. It does not use legacy
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

## Current verified Slice 3 baseline

- targeted tests: **294 passed**;
- Olo/Lever `submit_company` regression: **1 passed**;
- full pytest: **467 passed**, with 150 existing third-party `lxml` deprecation
  warnings;
- Ruff: **All checks passed**;
- MyPy: **Success — 28 source files**;
- Django system check: **0 issues**;
- migration check: **No changes detected**;
- `pip check`: **No broken requirements found**;
- `git diff --check`: **Pass**.

These checks used temporary database configuration. The working `db.sqlite3`
was not opened or used, and no job-source network requests were performed.

## Scope boundary

This evidence verifies Slice 3 Company multi-source orchestration and the
existing production Lever path. It does not verify or claim source-management
UI, Source Discovery, Darwinbox/JazzHR/LinkedIn adapters, Acuity production
integration, cross-source vacancy deduplication, or any future Slice 4 work.
