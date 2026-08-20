# LinkedIn Job Monitor

Internal Django application for monitoring company vacancies through
source-specific adapters while keeping normalization, persistence,
reconciliation, run history, and the UI source-neutral.

## Current source status

- **Current production-approved source:** Lever. Users paste a public jobs URL such
  as `https://jobs.lever.co/<site>` into Add source; the platform is detected
  automatically.
- **Current approved source:** Darwinbox. Its adapter uses a temporary normal
  headful system-Chrome session through Scrapling `DynamicFetcher`, opens the
  public candidate-v2 careers UI, and captures the listing/detail JSON emitted
  by that UI. It never imports a profile or cookies and does not use stealth,
  proxies, custom fingerprinting, or direct listing API replay. Darwinbox is a
  normal Add Source choice and is executable through Update jobs/Update all.
- **Current approved source:** JazzHR. Its plain-HTTP adapter accepts public
  `https://<tenant>.applytojob.com/apply` listings, loads each public detail
  page, and fails closed unless it can return the complete configured snapshot.
- **Current approved source:** DreamJobs. Its plain-HTTP adapter accepts public
  HTTPS `/jobs` career pages, including verified custom domains such as
  `https://careers.datasentics.com/jobs`. It verifies the platform from the
  Next.js snapshot and DreamJobs assets, then uses the same public GraphQL
  listing/detail contract as the career page with bounded pagination and
  request limits. The verified compatibility scope is Data Sentics' current
  DreamJobs variant, not every historical or future DreamJobs deployment.
- **Internal test source:** Fixture. It remains registered for deterministic
  offline pipeline tests, but is not offered for new user-managed companies.
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

Darwinbox execution additionally requires an installed system Google Chrome
and an interactive desktop session because its verified public SPA transport is
headful. A headless/server-only deployment must fail cleanly rather than fall
back to direct HTTP or stealth transport.

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

### Background concurrency

`JOB_MONITOR_BACKGROUND_MAX_WORKERS` controls source-run concurrency and
defaults to the SQLite-safe project limit of `2`; set it to `1` for sequential
execution. Values above `2` are rejected. `JOB_MONITOR_SQLITE_TIMEOUT_SECONDS`
controls SQLite's busy timeout and defaults to `30`. Fetch work may overlap,
while the short persistence/reconciliation write phase is serialized in the
process. Each worker closes stale Django connections before and after its task;
SQLite WAL mode is not enabled.

### Source Discovery

Company detail can discover a probable official careers source in the
background. Entering the company name is sufficient: production/development
name search uses Tavily's structured Search API with
`SOURCE_DISCOVERY_TAVILY_API_KEY` (never search-result HTML). Keyless mode is an
explicit, bounded diagnostic fallback only. Brave remains an explicitly
selectable legacy implementation. The official-domain field is only an
optional accelerator. Configuration, bounded limits, failure behavior, and
automatic-connection thresholds are documented in
[Production Source Discovery](docs/SOURCE_DISCOVERY.md).
Each explicit run inventories all existing and retained sources, scans current
evidence, and performs registry-driven searches only for missing adapters. The
Discovered tab keeps separate, deduplicated candidates with their origin and a
per-platform coverage summary; bounded/incomplete sweeps are labeled partial.

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
summary and one **Manage sources** dialog. Its **Connected** tab contains
URL-only auto-detected Add, Edit, Disconnect, Reconnect, and Delete; its
**Discovered** tab contains name-only
Discovery status, candidates, revalidation, connection actions, and
saved-evidence task drafts. See the
[multi-source architecture](docs/MULTI_SOURCE_ARCHITECTURE.md) for the exact
current and future boundaries.

## Documentation

- [Milestones and current status](docs/MILESTONES.md)
- [Backend and quality verification](docs/BACKEND_VERIFICATION.md)
- [Multi-source architecture](docs/MULTI_SOURCE_ARCHITECTURE.md)
- [Production Source Discovery](docs/SOURCE_DISCOVERY.md)
- [Project specification](docs/PROJECT_SPEC.md)
- [Scrapling guide and evidence](docs/SCRAPLING_GUIDE.md)
- [Canonical historical LinkedIn pagination diagnostic](docs/diagnostics/linkedin-pagination-2026-08-05.md)
