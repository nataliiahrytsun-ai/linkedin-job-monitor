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
- Registry-backed production Source selection: Lever is selectable when adding
  a CompanySource; Fixture is internal-only. Company create/edit no longer
  manages source-specific fields.
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

## Staged multi-source architecture extension

The original Lever Milestone 3 scope remains completed. A later Acuity source
audit showed that one monitored Company can have multiple independent ATS
feeds, so the additional architecture extension is being delivered in staged
slices:

- **Slice 1 — schema foundation: Completed.** Added `CompanySource`, nullable
  ownership links on `JobPosting` and `ScrapeRun`, and deterministic backfill of
  legacy source configurations.
- **Slice 2 — source ownership: Completed.** Pipeline context, registry
  selection, persistence identity, ScrapeRun ownership, and successful-snapshot
  reconciliation are scoped to one CompanySource. The transitional Company
  entry point resolves exactly one approved/active source and fails closed when
  resolution is ambiguous. Its quality gate passed with 444 tests and the
  existing 150 third-party `lxml` deprecation warnings; Ruff, MyPy, Django,
  migration, dependency, and diff checks passed.
- **Slice 3 — lifecycle/background orchestration: Completed.** Source-level
  RUNNING protection, background ownership, independent execution and failure
  isolation, Company orchestration, aggregate status/time, multi-source-aware
  polling, Update jobs, and Update all are implemented. Its quality gate passed
  with 467 tests and the existing 150 third-party `lxml` warnings; all other
  checks passed.
- **Slice 4 — source-management UI: Completed.** Company detail now provides a
  compact source summary and responsive Manage/Add/Edit dialogs. Users can add
  registry-approved production sources, edit source configuration without
  changing immutable platform provenance, and independently activate or
  deactivate eligible sources. RUNNING-source safety and POST-only state
  changes are enforced. Its quality gate passed with 487 tests and the existing
  150 third-party `lxml` warnings; all other checks passed.

The bounded **Darwinbox adapter and headful-browser transport are now
implemented**. A fresh plain headful system-Chrome run rendered the public
Acuity SPA and received its initial listing without private state or interaction.
Production transport now opens the candidate-v2 UI through Scrapling
`DynamicFetcher`, captures SPA data responses, and paginates via visible Load
More. Darwinbox is a selectable approved/active source and participates in
Update jobs/Update all. Complete-snapshot enforcement, request accounting, and
source-scoped pipeline/reconciliation remain unchanged. This does not complete
Acuity multi-source monitoring or claim support for every Darwinbox installation.

Source Discovery, JazzHR and LinkedIn adapters, cross-source vacancy
deduplication, hard source deletion, run cancellation, and final legacy cleanup
are not implemented. Lever remains unchanged and selectable.
The current architecture and boundaries are documented in
[`docs/MULTI_SOURCE_ARCHITECTURE.md`](MULTI_SOURCE_ARCHITECTURE.md).

LinkedIn remains the original product target, but it is not a current
production source: there is no production LinkedIn adapter, registry key, or
Company dropdown option. Feasibility, access, and authorization must be decided
before any production LinkedIn integration. Historical spike code and reports
must not be described as production support.
