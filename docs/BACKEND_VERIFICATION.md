# Backend and quality verification

## Verified production flow

The current source-owned path is:

```text
Company / UI action -> exactly one resolved CompanySource
                    -> background execution -> immutable source registry
                    -> SourceAdapter -> SourceBatch -> normalization
                    -> source-scoped persistence -> source-scoped reconciliation
                    -> source-owned ScrapeRun
                    -> Dashboard / Company / ScrapeRun UI and status polling
```

`JobPosting` identity and successful-snapshot reconciliation are isolated by
`CompanySource`. Tests cover equal external IDs in two sources of one Company,
empty-snapshot isolation in both source directions, foreign-source seen-ID
rejection, explicit source URL delivery to the adapter, and fail-closed legacy
resolution with zero or multiple executable sources.

The shared pipeline supports two deliberately different source roles:

- `lever`: current production and user-selectable adapter. The executable
  CompanySource stores `source="lever"` and a public URL such as
  `https://jobs.lever.co/olo`; matching legacy Company fields remain during the
  staged migration.
- `fixture`: registered internal/test adapter. It reads local synthetic data,
  reports `requests_made=0`, and remains available to pipeline tests, but it is
  excluded from Add Company and rejected as a user-assigned source.

LinkedIn has no production adapter or registry key. Its spike and diagnostic
artifacts are historical feasibility evidence only.

## Lever multi-page integration proof

`test_lever_multi_page_submission_persists_complete_snapshot_without_network`
in `tests/test_background.py` enters through the production background
submission path. The registry selects `LeverSourceAdapter`; injected fake HTTP
returns the existing two JSON fixtures:

```text
request 1: limit=2, skip=0 -> lever-1, lever-2
request 2: limit=2, skip=2 -> lever-3
```

The test verifies exactly two calls, three normalized/persisted jobs, the
expected source IDs and titles, reconciliation of the complete snapshot, a
terminal `SUCCESS` ScrapeRun, and `requests_made=2`. It uses the pytest
temporary database and performs no real network request.

Additional coverage verifies adapter URL validation and mapping, pagination
termination and limits, duplicate IDs, timeout/error conversion, registry
routing, fixture execution, persistence updates, partial/failed runs,
reconciliation, background duplicate-run protection, and UI status contracts.

## Reproducible checks

Install the runtime and pinned development tools through the dev requirements:

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

Bootstrap tests override Django's database configuration with a unique
temporary SQLite path. The working repository `db.sqlite3` is not a test
precondition and must not be opened, migrated, replaced, or removed for the
verification gate.

## Current verified baseline

The verified Slice 2 ownership baseline is:

- `pytest`: **444 passed**, with 150 existing third-party `lxml` deprecation
  warnings;
- Ruff: **All checks passed**;
- MyPy: **Success, no issues found**;
- `pip check`: **No broken requirements found**;
- Django system check: **0 issues**;
- migration check: **No changes detected**;
- `git diff --check`: **Pass**.

## Scope boundary

This evidence verifies the current application, the production Lever path, and
Slice 2 source ownership. It does not verify future Company-wide multi-source
orchestration, Source Discovery, Darwinbox/JazzHR adapters, or a production
LinkedIn integration. It does not authorize LinkedIn collection or prove
collection of every vacancy exposed by an external service. Live Olo
verification is an already-completed manual check; normal automated checks stay
offline.
