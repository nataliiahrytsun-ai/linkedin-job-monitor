# LinkedIn Job Monitor

<!-- CURRENT-IMPLEMENTATION-2026-08-22 -->

## Current implementation status ? 2026-08-22

The current executable source stack includes **Lever, Darwinbox, JazzHR,
DreamJobs, Zoho Recruit, and Generic**. `fixture` remains internal/test-only.
LinkedIn is not a production source.

`CompanySource` is the execution and ownership boundary. **Update jobs** runs
all approved and active executable sources for the selected Company through the
shared normalization, persistence, reconciliation, and `ScrapeRun` pipeline.

### Generic public-careers fallback

Generic is a reusable fallback for eligible public careers pages; it is not a
company-specific adapter and is not intended to require one adapter per
company. It currently supports deterministic listing extraction, semantic
HTML / `JobPosting` JSON-LD detail metadata, common WordPress/Elementor vacancy
content, and explicitly labelled HR metadata.

Generic deliberately does **not** guess ambiguous HR fields from prose. A
missing value is stored as null and displayed as `?`.

The shared persisted HR fields now include `employment_type`,
`seniority_level`, and `compensation_text` in addition to the existing
location/country, publication date, description, and related fields.

### Progressive Generic detail enrichment

Generic detail requests are separately bounded to **50 detail pages per source
run**.

The listing can therefore contain more jobs than are detail-enriched in one
run. For a large Generic source, later successful **Update jobs** runs skip
already enriched URLs, preserve their stored detail metadata, and continue
with the remaining jobs.

Consequences:

- more than 50 vacancies: detail enrichment can require several successful
  runs;
- 50 or fewer vacancies: all discovered detail pages should normally be
  attempted in one run;
- a blank field after enrichment does not necessarily indicate failure; the
  public source may not publish that field in a trustworthy extractable form;
- repeated runs do not invent metadata that the source does not expose.

This progressive enrichment limit is independent from listing pagination and
listing request accounting.


Internal Django application for monitoring company vacancies through
source-specific adapters while keeping normalization, persistence,
reconciliation, run history, and the UI source-neutral.

## Current source status

The current executable source stack is:

- **Lever** ? production adapter for public `jobs.lever.co` sources.
- **Darwinbox** ? production adapter using the verified normal headful
  system-Chrome flow through Scrapling `DynamicFetcher`.
- **JazzHR** ? production plain-HTTP adapter for public
  `<tenant>.applytojob.com/apply` sources.
- **DreamJobs** ? production plain-HTTP adapter for validated public `/jobs`
  career pages, including verified custom domains.
- **Zoho Recruit** ? production plain-HTTP adapter for validated public Zoho
  Recruit career sites using the embedded published jobs snapshot.
- **Generic** ? executable fallback for eligible public careers pages that do
  not require a dedicated ATS adapter. It is connected through discovery /
  auto-detection and is intentionally not exposed as a manual platform choice.
- **Fixture** ? internal deterministic test source only.

LinkedIn is **not** a production source. Historical LinkedIn diagnostics and
pagination experiments remain technical evidence only and do not authorize
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
