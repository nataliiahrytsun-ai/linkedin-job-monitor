# Project goal

Create an internal Django application that gets company vacancies from a
permitted source, saves and updates them without creating duplicates, tracks
vacancies that disappear, and shows the results to users.

# Milestone 1 — Technical spike

**Status:** Completed

## Goal

Verify whether public LinkedIn vacancy extraction and limited pagination were
technically feasible within the approved diagnostic boundaries.

## Result

- Live extraction was technically verified.
- Limited pagination was technically verified within the strict diagnostic
  request/page limits.
- Complete collection of every LinkedIn vacancy was not proven.
- No production LinkedIn adapter was implemented or authorized.
- Further LinkedIn work requires a separate feasibility/access decision.

The canonical historical evidence is recorded in
[`docs/diagnostics/linkedin-pagination-2026-08-05.md`](diagnostics/linkedin-pagination-2026-08-05.md).

# Milestone 2 — Source-neutral backend and production source

**Status:** Completed

## Result

- Django and SQLite setup.
- `Company`, `JobPosting`, and `ScrapeRun` models.
- Source adapter contract and immutable registry APIs.
- Normalization, stable identity hashing, deduplication, and safe identity
  upgrades.
- Creation, updating, and reconciliation of job postings.
- Complete `ScrapeRun` lifecycle with `SUCCESS`, `PARTIAL`, and `FAILED`
  outcomes and request accounting.
- `ACTIVE` to `NOT_FOUND` transition after two successful misses.
- Controlled background execution and duplicate active-run protection.
- Internal `fixture` adapter for deterministic offline testing.
- Production `lever` adapter using Scrapling's plain HTTP session.
- Lever URL/site validation, mapping, bounded offset pagination,
  deduplication, error handling, and request counting.
- Offline integration proof for two Lever pages (`skip=0`, then `skip=2`),
  three persisted jobs, terminal `SUCCESS`, and `requests_made=2` through the
  real registry/background/pipeline path.

The shared pipeline did not need a Lever-specific rewrite. Details and
repeatable checks are in
[`docs/BACKEND_VERIFICATION.md`](BACKEND_VERIFICATION.md).

# Milestone 3 — User interface and operational verification

**Status:** Completed for the current approved Lever scope

## Result

- Company add, edit, and detail views.
- Registry-backed production Source dropdown: Lever is selectable; Fixture is
  internal-only. Existing fixture companies can be edited without silently
  changing their source.
- Vacancy list, filters, detail display, and active-job information.
- Company-specific **Update jobs** and global **Update all** actions using the
  controlled background executor.
- Dashboard counters and Run Status, including **Running now**.
- ScrapeRun history with desktop, tablet, and mobile presentations.
- Lightweight read-only polling for current activity, ScrapeRun history, and
  company refresh without creating scraping work from status endpoints. The
  Dashboard can show a newly running run and return **Running now** to zero
  after completion without a manual page refresh.
- Temporary-database bootstrap isolation and reproducible development tooling.
- Final quality baseline: 427 tests passed; Ruff, MyPy, Django system checks,
  dependency consistency, migration checks, and diff checks passed. The 150
  pytest warnings are existing third-party `lxml` deprecation warnings.

Historical manual verification of the Olo Lever company confirmed the live
production adapter path. Automated verification remains offline and uses fake
HTTP responses; documentation closeout does not repeat live requests.

# Follow-up scope

LinkedIn remains the original product target, but it is not a current
production source: there is no production LinkedIn adapter, registry key, or
Company dropdown option. Feasibility, access, and authorization must be decided
before any production LinkedIn integration. Historical spike code and reports
must not be described as production support.
