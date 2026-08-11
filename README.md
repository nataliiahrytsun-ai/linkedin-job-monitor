# LinkedIn Job Monitor

Internal Django application for monitoring company vacancies through
source-specific adapters while keeping normalization, persistence,
reconciliation, run history, and the UI source-neutral.

## Current source status

- **Current production-approved source:** Lever. Users add a CompanySource through the
  source-management UI, select `Lever`, and configure a jobs URL in the form
  `https://jobs.lever.co/<site>`.
- **Implemented and visible, but live access unavailable:** Darwinbox.
  `DarwinboxSourceAdapter` is offline-tested against the observed Acuity
  contract. Source management explains its current status, but normal Add
  Source and Company-level execution exclude it. Direct automated HTTP listing
  access currently receives Cloudflare 403; a clean browser context receives a
  minimal HTML document without SPA bootstrap assets. Further transport
  investigation is deferred. This does not mean the adapter is broken or that
  Darwinbox is permanently unavailable.
- **Internal test source:** Fixture. It remains registered for deterministic
  offline pipeline tests, but is not offered for new user-managed companies.
- **Audited/planned, not implemented:** JazzHR.
- **LinkedIn production status:** Not implemented; feasibility/access follow-up
  and approval are blocked pending a separate decision. The LinkedIn spike and
  limited pagination diagnostic are
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

The source-owned production flow is:

```text
Company / UI action -> submit all approved active CompanySources
                    -> source registry -> SourceAdapter -> SourceBatch
                    -> source-scoped normalization, persistence, reconciliation
                    -> one ScrapeRun per source -> multi-source-aware polling
```

One Company can own and manage multiple CompanySource configurations, and one
platform adapter can serve many companies. Background execution, run ownership,
and reconciliation are source-scoped; a Company update can launch several
eligible sources independently. Company detail provides a compact source
summary plus Add, Edit, Activate, and Deactivate workflows. See the
[multi-source architecture](docs/MULTI_SOURCE_ARCHITECTURE.md) for the exact
current and future boundaries.

## Documentation

- [Milestones and current status](docs/MILESTONES.md)
- [Backend and quality verification](docs/BACKEND_VERIFICATION.md)
- [Multi-source architecture](docs/MULTI_SOURCE_ARCHITECTURE.md)
- [Project specification](docs/PROJECT_SPEC.md)
- [Scrapling guide and evidence](docs/SCRAPLING_GUIDE.md)
- [Canonical historical LinkedIn pagination diagnostic](docs/diagnostics/linkedin-pagination-2026-08-05.md)
