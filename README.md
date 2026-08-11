# LinkedIn Job Monitor

Internal Django application for monitoring company vacancies through
source-specific adapters while keeping normalization, persistence,
reconciliation, run history, and the UI source-neutral.

## Current source status

- **Current production source:** Lever. User-managed companies select `Lever`
  and store `source="lever"`; the jobs URL has the form
  `https://jobs.lever.co/<site>`.
- **Internal test source:** Fixture. It remains registered for deterministic
  offline pipeline tests, but is not offered for new user-managed companies.
- **LinkedIn production status:** Not implemented; feasibility/access follow-up
  is required. The LinkedIn spike and limited pagination diagnostic are
  historical technical evidence, not a production adapter or authorization for
  production collection.

## Setup

Use Python 3.12 or a compatible newer version and install the runtime
dependencies:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py runserver
```

For development and verification, install the reproducible dev toolchain. The
dev requirements include the runtime requirements:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m mypy
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py makemigrations --check --dry-run
```

Tests use an isolated temporary database and offline HTTP fakes/fixtures. They
must not use the repository's working `db.sqlite3` or make live job-board
requests.

## Architecture

The production flow is:

```text
Company / UI action -> background execution -> source registry -> SourceAdapter
                    -> SourceBatch -> normalization -> persistence
                    -> reconciliation -> ScrapeRun -> UI / status polling
```

Lever is integrated only at the adapter/registry seam. The shared pipeline is
also exercised by the internal fixture adapter. To add another approved
production source, implement the existing `SourceAdapter` contract, test it
offline, register it with the appropriate user-selectable metadata, and keep
source-specific fetching and mapping out of the shared pipeline.

## Documentation

- [Milestones and current status](docs/MILESTONES.md)
- [Backend and quality verification](docs/BACKEND_VERIFICATION.md)
- [Project specification](docs/PROJECT_SPEC.md)
- [Scrapling guide and evidence](docs/SCRAPLING_GUIDE.md)
- [Canonical historical LinkedIn pagination diagnostic](docs/diagnostics/linkedin-pagination-2026-08-05.md)
