# Milestones

This document turns the requirements in `docs/PROJECT_SPEC.md` into delivery
stages. A limited diagnostic result is evidence, not permission for production
use. Source-neutral tracks may finish independently of blocked production-source
tracks.

## Status vocabulary

| Status | Meaning |
|---|---|
| **Planned** | Scope and gates are defined, but implementation has not started. |
| **In Progress** | Implementation has started, but one or more required gates remain incomplete. |
| **Completed** | The track's scope is implemented and its automated, integration, and manual gates have passed with recorded evidence. |
| **Blocked** | Work is defined but cannot proceed without an external decision, permission, or prerequisite. |
| **Provisional** | The outline is not yet an approved implementation plan and still needs scope decisions. |

A blocked track does not change the status of an independent track. Manual
checks use `Pass`, `Fail`, or `Blocked`; a track cannot be `Completed` while one
of its required checks is failed or unreported.

---

## Milestone 1 — Scrapling Research and LinkedIn Technical Spike

**Status:** Completed

**Historical completion outcome:** Not feasible through compliant public access
under the original ordinary-operation robots gate.

### Goal

Determine whether the public Acuity Analytics LinkedIn Jobs source could be
processed within the project's technical and compliance boundaries, document
Scrapling usage, and establish tested extraction and pagination evidence.

### Historical scope

- Analyze the Scrapling repository and official documentation.
- Create `docs/SCRAPLING_GUIDE.md`.
- Test the public Acuity Analytics LinkedIn Jobs URL.
- Determine which Scrapling fetcher is suitable.
- Investigate pagination, lazy loading, and job detail pages.
- Save appropriate fixtures and add initial extraction tests.
- Create `docs/SPIKE_REPORT.md`.
- Make a documented feasibility decision.

### Historical early-termination rule

The original technical spike could terminate before requesting the configured
target when live `robots.txt` disallowed it for the ordinary crawler user agent.
That was a complete historical outcome when the robots request, applicable
rule, target URL, counts, timings, environment, and limitation were recorded;
the target, pagination, and detail pages were not requested; offline policy and
fixture tests passed; and no workaround was implemented.

The rule applied to the original milestone run. LinkedIn's `robots.txt`
disallowed the target for the ordinary crawler, so that run stopped before the
target request and recorded `Not feasible through compliant public access`.

### Historical completion criteria

- The no-login access boundary was tested or the early-termination rule applied.
- Tested fetchers and outcomes were documented.
- Public fields, pagination, and detail behavior were documented when permitted,
  or their unverified status was recorded.
- Counts, timings, failures, and limitations were recorded.
- Initial fixture extraction tests passed.
- No access-control circumvention was implemented.
- `docs/SCRAPLING_GUIDE.md` and `docs/SPIKE_REPORT.md` existed.

### Current evidence boundary

The later, explicitly bounded diagnostics supplement but do not rewrite the
historical run:

- **Live extraction:** Verified.
- **Continuation endpoint:** Verified.
- **Pagination:** Verified for the limited diagnostic.
- **Complete vacancy collection:** Not verified.
- **Production LinkedIn scraping:** Not authorized.
- **Milestone status:** Completed.

The canonical evidence is
[`docs/diagnostics/linkedin-pagination-2026-08-05.md`](diagnostics/linkedin-pagination-2026-08-05.md).
The limited post-fix validation made four target requests for four pages, used
offsets 25, 50, and 75, and added 22 IDs outside the saved 60-ID baseline before
stopping at `page_limit` without a technical block. It does not claim that all
displayed vacancies were collected and does not authorize another live run,
production LinkedIn scraping, or full server-side scraping.

### Definition of done

Milestone 1 remains historically `Completed`. No further live diagnostic is a
completion requirement or authorized by this milestone.

---

## Milestone 2 — Backend Delivery

### Goal

Build a source-neutral backend that can normalize, identify, store, update, and
reconcile job data offline, while keeping any production-source integration
behind its own permission and evidence gate.

Milestone 2 has two independent tracks. Track 2-B does not block completion of
Track 2-A.

## Milestone 2-A — Source-neutral Backend

**Status:** In Progress

This track is the current Milestone 2 delivery scope. It can become `Completed`
after its own automated, integration, and manual gates pass, regardless of
whether a production source has been selected.

### Prerequisites

- Milestone 1 is complete.
- Python 3.12 and Django 5.2 are available.
- SQLite and the source-neutral data-model contract are accepted.
- No production source is required for this track.

### Scope

- Django bootstrap, models, and migrations.
- Source-neutral normalization and stable identity hashing.
- Atomic single-job persistence.
- `ScrapeRun` lifecycle and counters.
- Successful-run reconciliation and configurable inactivity behavior.
- Fixture-based end-to-end backend pipeline.
- Recoverable per-job error handling.
- Controlled, non-blocking background execution.
- Backend unit and integration tests.
- Installation, operational documentation, and manual milestone checks.

### Out of scope

- Production LinkedIn scraping or an unapproved production adapter.
- UI, scheduler, and periodic execution.
- Celery, Redis, PostgreSQL, or a separate frontend.
- Login automation, private cookies, CAPTCHA handling, or circumvention.
- Storage of complete source HTML.

### Blockers

There is no external blocker for Track 2-A. A production-source decision is a
Track 2-B blocker only.

### Work packages

| Work package | Status | Result or remaining outcome | Commit or evidence |
|---|---|---|---|
| Django bootstrap | Completed | Django project, apps, SQLite, and offline smoke tests | `91f15e1` — `build: bootstrap Django project with SQLite` |
| Core models and migrations | Completed | `Company`, `JobPosting`, `ScrapeRun`, constraints, and indexes | `7d030e1` — `feat: add core data models and initial migrations` |
| Normalization and identity hashing | Completed | Immutable DTO, canonical normalization, `content_hash`, and `dedupe_key` | `308eeb3` — `feat: add normalized job data and identity hashing` |
| Single-job persistence | Completed | Atomic `CREATED`/`UPDATED`/`UNCHANGED` persistence and URL-to-ID upgrade | `d261edf` — `feat: add job posting persistence service` |
| ScrapeRun lifecycle | Planned | Atomic start/finalization, status transitions, counters, and errors | Not implemented |
| Successful-run reconciliation | Planned | Reconcile unseen jobs only after complete successful runs | Not implemented |
| Configurable inactivity behavior | Planned | Configurable successful-miss threshold without schema changes | Not implemented |
| Fixture-based backend pipeline | Planned | Offline source-neutral fixture input through normalization and persistence | Not implemented |
| Recoverable per-job errors | Planned | One item failure does not discard unrelated successful work | Not implemented |
| Controlled background execution | Planned | Non-blocking invocation and duplicate-company-run control | Not implemented |
| Backend integration tests | Planned | Repeatable complete offline backend flows | Not implemented |
| Operational documentation | Planned | Installation, migration, execution, and troubleshooting instructions | Not implemented |
| Manual milestone gate | Planned | Recorded manual `Pass`/`Fail`/`Blocked` results | Not performed |

### Required lifecycle and reconciliation behavior

- Starting and finalizing a run is explicit and atomic.
- Only one `RUNNING` run may exist for a company.
- Terminal statuses are `SUCCESS`, `PARTIAL`, and `FAILED`.
- Terminal runs record finish time, duration, counters, and useful errors.
- Only a complete `SUCCESS` run increments absence counters.
- `PARTIAL` and `FAILED` never increment successful-miss counters.
- The inactivity threshold is service configuration, not database schema; the
  accepted initial default is two consecutive complete successful misses.
- Seeing a job again resets its successful-miss counter.
- Explicit `CLOSED` takes priority over `NOT_FOUND`.
- A failed run never marks a job inactive.

### Acceptance criteria

- New normalized jobs are created without duplicates.
- Changed jobs are updated and unchanged jobs refresh `last_seen_at`.
- URL-only identities can upgrade safely to stable source IDs.
- Run history and counters represent committed outcomes accurately.
- Successful reconciliation follows the threshold rules above.
- A recoverable item error does not abort unrelated successful items.
- Duplicate simultaneous company runs are prevented.
- Background execution does not block the future UI request path.
- Source-specific assumptions do not leak into normalization or persistence.
- No network is required to prove this track.

### Automated test gate

- Unit coverage for lifecycle transitions, counters, reconciliation, threshold
  reset, `CLOSED` priority, per-item errors, and duplicate runs.
- Existing model, normalization, identity, and persistence tests remain green.
- `manage.py check`, full pytest, Ruff, and configured MyPy strict pass.
- `makemigrations --check --dry-run` reports no missing migration.
- Migrations apply to a clean temporary SQLite database.

### Integration test gate

A saved synthetic fixture flow must demonstrate company/run creation, multiple
jobs, create/update/unchanged outcomes, identity deduplication, one recoverable
item failure, correct counters/status, successful reconciliation, and safety of
partial/failed runs. It must not use the network.

### Manual test gate

- Apply migrations to a fresh SQLite database — unreported.
- Run the fixture pipeline twice and inspect create/update results — unreported.
- Verify duplicate-run rejection — unreported.
- Verify failed and partial runs do not mark jobs inactive — unreported.
- Verify background invocation returns without waiting for the run — unreported.
- Confirm no production source request was made — unreported.

### Definition of done

Track 2-A becomes `Completed` when all planned work packages are implemented and
its automated, integration, and manual gates pass. Track 2-B may remain
`Blocked` without hiding or preventing that completion.

## Milestone 2-B — Permitted Production Source Integration

**Status:** Blocked

### Prerequisites

- The team selects a production data source.
- Automated retrieval from that source is explicitly approved.
- Access, authentication, robots, rate, and retention boundaries are recorded.
- The Track 2-A adapter-facing contracts are stable.

### Scope

- Source-specific validation and adapter mapping into `NormalizedJobPosting`.
- Minimal approved fixtures and extraction tests.
- Permitted extraction and pagination/continuation verification.
- Bounded source-specific request and error behavior.
- End-to-end validation through the source-neutral backend.

### Out of scope

- Treating the limited LinkedIn diagnostic as production permission.
- Login automation, private cookies, access-control circumvention, or aggressive
  proxy/IP rotation.
- Unbounded scraping or hard-coding the backend for one company.
- Celery, Redis, PostgreSQL, scheduler, or UI.

### Blockers

- A permitted production source has not been selected.
- Automated retrieval has not been approved.

The limited LinkedIn diagnostic is technical evidence only. It is not approval
for a production LinkedIn adapter or server-side scraping.

### Work packages

| Work package | Status | Required outcome |
|---|---|---|
| Select and approve production source | Blocked | Documented source and permission boundary |
| Define source adapter | Blocked | Source response maps to the source-neutral DTO |
| Add approved fixtures and extraction tests | Blocked | Offline source-specific evidence |
| Verify production extraction | Blocked | Approved live or supplied-data evidence |
| Verify bounded pagination/continuation | Blocked | Safe termination and completeness boundary |
| Validate production backend flow | Blocked | Source through normalization, persistence, and run lifecycle |
| Document operating limits | Blocked | Timeouts, delays, retries, limits, and failure behavior |

### Acceptance criteria

- Source use and automated retrieval are explicitly permitted.
- Missing optional fields remain `None`; identities are stable; no restricted
  or private data is processed.

### Automated test gate

- Adapter parsing, DTO mapping, URL validation, pagination termination, and
  source-specific errors are covered without live network access.
- Existing Track 2-A checks remain green.

### Integration test gate

- Approved fixtures pass through normalization, persistence, run lifecycle,
  and reconciliation.
- Production-specific integration is performed only after source approval.

### Manual test gate

- Permission and operating boundaries are reviewed and recorded.
- Any authorized live test is separately approved, bounded, disabled by
  default, and records requests, results, and termination behavior.
- No restricted/private data or circumvention mechanism is used.

### Definition of done

Track 2-B becomes `Completed` only after source approval, adapter implementation,
and production end-to-end evidence. Its blocked status does not block Track 2-A.

### Milestone 2 implementation decomposition

The tasks below are an implementation decomposition, not new original customer
requirements. Each implementation commit must include focused tests where it
introduces behavior.

| Proposed commit | Work package | Plain-language result | Dependencies | Not included |
|---|---|---|---|---|
| `feat: add scrape run lifecycle service` | Run lifecycle | Start and finish runs with correct statuses and counters | Existing models | Reconciliation, fetching |
| `feat: reconcile jobs after successful runs` | Reconciliation | Track successful misses without harming failed runs | Lifecycle and persistence | Production source |
| `feat: add fixture-based backend pipeline` | Pipeline | Process offline fixture jobs end to end | Lifecycle and reconciliation | Production adapter |
| `feat: isolate recoverable job processing errors` | Error handling | Preserve successful items when one item fails | Fixture pipeline | Retry worker |
| `feat: add controlled background execution` | Background execution | Return control promptly and prevent duplicate runs | Stable pipeline | Celery, scheduler |
| `test: add backend pipeline integration coverage` | Integration gate | Prove repeated complete offline flows | All Track 2-A services | Live source |
| `docs: document backend operation and checks` | Documentation | Reproducible installation and operation | Stable Track 2-A | Production claims |
| `feat: add permitted source adapter` | Track 2-B adapter | Convert an approved source to normalized jobs | Source approval | Other providers |
| `test: validate permitted source backend flow` | Track 2-B acceptance | Prove the approved source end to end | Adapter and permission | Unapproved live tests |

---

## Milestone 3 — Django UI and Final Integration

### Goal

Provide a compact internal Django Templates/HTMX UI for companies, jobs, and
run history, while keeping final production-source acceptance in a separately
blocked track.

Milestone 3 has two independent tracks. Track 3-B does not prevent Track 3-A
from recording its actual completion.

## Milestone 3-A — Fixture/Source-neutral Django UI

**Status:** Planned

Track 3-A can become `Completed` after its own UI automated, integration, and
manual gates pass, regardless of production-source selection.

### Prerequisites

- Track 2-A persistence and lifecycle contracts are stable.
- Controlled background execution exposes a UI-callable API.
- The fixture-based backend pipeline is available.

### Scope

- Django Templates and limited HTMX interactions.
- Dashboard and company management.
- Job list, required filters, and job detail.
- `ScrapeRun` history, counters, and errors.
- Start-run actions and non-blocking status polling.
- Empty, loading, success, partial, and failure states.
- UI unit/integration tests, responsive checks, and documentation.

### Out of scope

- A separate frontend framework.
- Scraping or persistence logic in views/templates.
- Scheduler, Celery, Redis, or PostgreSQL.
- Production-source assumptions.
- Login, users, roles, or permissions without a separate team requirement.

### Blockers

Implementation has not started. Its technical prerequisites are remaining Track
2-A packages, not the production-source decision.

### Work packages

| Work package | Status | Result | Dependencies | Acceptance evidence |
|---|---|---|---|---|
| Company management | Planned | List/create/edit/deactivate companies | Company model | Form/view tests and manual flow |
| Dashboard | Planned | Required company, job, success, running, and failure metrics | Lifecycle queries | Query/view tests |
| Job list and filters | Planned | Required columns and combined filters | JobPosting data | Filter/view tests |
| Job detail | Planned | All stored fields and full description | JobPosting data | Template tests with missing fields |
| ScrapeRun history/errors | Planned | Run status, counters, duration, and errors | Lifecycle service | Query/template tests |
| Start-run actions | Planned | One-company and all-active-company actions | Background API | POST and duplicate-run tests |
| Non-blocking status updates | Planned | Limited HTMX polling to terminal status | Background API | HTMX response tests |
| UI states | Planned | Empty/loading/success/partial/failure rendering | All views | Template and manual checks |
| UI integration tests | Planned | Fixture-backed user flows | All UI packages | Django integration suite |
| Installation/final UI docs | Planned | Reproducible setup and usage | Stable UI | Fresh-install walkthrough |

Required job filters from `PROJECT_SPEC.md` are company, company type, country,
location, job title/free text, status, publication date range, and workplace
type (`remote`, `hybrid`, `onsite`).

### Acceptance criteria

- Companies can be added, edited, and deactivated; physical deletion is not
  required.
- Dashboard presents the required metrics from stored records.
- Job list, all specified filters, and job detail work with optional fields.
- Run history and useful errors are visible.
- Run actions use the service layer, return without blocking, and report
  duplicate-run rejection.
- Polling ends at a terminal run state.
- Empty, loading, partial, and failure states are understandable.
- Views and templates remain source-neutral.

### Automated test gate

- Company forms/views, dashboard queries, every required filter, job detail,
  run history, start actions, duplicate runs, HTMX polling, and UI state
  templates are covered.
- Full pytest, Ruff, configured MyPy strict, and `manage.py check` pass.
- Authorization tests are added only if an authorization model is separately
  approved.

### Integration test gate

A fixture-backed flow must configure a company, start source-neutral processing,
observe a non-blocking running state, reach a terminal `ScrapeRun`, display
created/updated jobs, apply required filters, open a job detail, and display a
recoverable item error.

### Manual test gate

- Empty and populated dashboard.
- Company create/edit/deactivate.
- Start, running, terminal, duplicate-start, partial, and failed states.
- Combined job filters, long descriptions, and missing optional values.
- Keyboard/basic accessibility and narrow/mobile viewport.
- Fresh local installation walkthrough.

### Definition of done

Track 3-A becomes `Completed` when all planned UI packages and its automated,
integration, and manual gates pass. Track 3-B may remain `Blocked` without
hiding or preventing that completion.

## Milestone 3-B — Final Production Integration and Acceptance

**Status:** Blocked

### Prerequisites

- Track 2-B is complete.
- Track 3-A is complete or sufficiently stable for final acceptance.

### Scope

- Display actual permitted-source companies, jobs, runs, and errors.
- Trigger only the approved production adapter.
- Verify non-blocking production execution and UI status updates.
- Complete final operational and troubleshooting documentation.

### Out of scope

- Any unapproved source or additional LinkedIn live run.
- Authentication/authorization unless separately required.
- A different deployment stack, scheduler, or queue system.

### Blockers

Track 2-B is blocked because no permitted production source or automated
retrieval approval exists.

### Work packages

| Work package | Status | Required evidence |
|---|---|---|
| Production UI data flow | Blocked | Approved-source records display correctly |
| Production run action/status | Blocked | Approved adapter executes without blocking UI |
| Production error display | Blocked | Real permitted-source errors are understandable |
| Final end-to-end acceptance | Blocked | Create/update/dedupe/reconcile/filter flow passes |
| Final operations documentation | Blocked | Approved-source setup and limits are reproducible |

### Acceptance criteria

- Approved-source jobs and runs appear correctly without duplicates.
- Repeated runs update existing records and follow reconciliation rules.
- Production run actions remain non-blocking and expose useful failures.
- Required filters work with production-derived records.

### Automated test gate

- All Track 3-A UI tests remain green.
- Approved adapter/UI boundary behavior is covered without unapproved network
  access.

### Integration test gate

- The permitted source passes through adapter, backend services, run lifecycle,
  and UI using approved evidence.
- Create, update, dedupe, reconcile, filter, detail, and error flows pass.

### Manual test gate

- Approved production execution, status updates, responsive UI, filters, and
  visible errors are checked and recorded.
- Operating limits and the absence of restricted behavior are confirmed.

### Definition of done

Track 3-B becomes `Completed` only after Track 2-B and its own final production
acceptance gates complete. Its status does not change Track 3-A's status.

### Authentication and authorization open decision

`PROJECT_SPEC.md` describes an internal application but does not define an
authentication or authorization model. Login, users, roles, and permissions are
not in the current approved scope and must not be added without a separate team
requirement.

### Milestone 3 implementation decomposition

These commit-sized tasks are implementation decomposition, not new original
customer requirements.

| Proposed commit | Work package | Plain-language result | Dependencies | Not included |
|---|---|---|---|---|
| `feat: add company management UI` | Company UI | Create, edit, list, and deactivate companies | Company model | Run controls |
| `feat: add job list and filters` | Job list | Search and filter stored jobs | JobPosting data | Job detail |
| `feat: add job detail view` | Job detail | Display the complete stored job | Job list | Fetching |
| `feat: add scrape run history UI` | Run history | Show counters, statuses, and errors | Lifecycle service | Triggering |
| `feat: add non-blocking run controls` | Actions/status | Start runs and poll terminal state | Background API | Scheduler |
| `feat: add dashboard metrics and UI states` | Dashboard/states | Required metrics and clear empty/failure states | Stable queries | New business metrics |
| `test: add Django UI integration coverage` | UI gate | Prove fixture-backed user flows | All Track 3-A views | Production source |
| `docs: complete installation and operations guide` | Documentation | Explain setup, use, and troubleshooting | Stable application | Unsupported stack |
| `test: validate permitted source through UI` | Track 3-B acceptance | Prove the approved source in the final UI | Tracks 2-B and 3-A | Unapproved sources |

---

## Traceability matrix

| Requirement | Source section | Milestone | Work package | Current status | Commit or blocker | Acceptance evidence |
|---|---|---|---|---|---|---|
| Django and SQLite | PROJECT_SPEC: Technical stack / §4 | 2-A | Bootstrap | Completed | `91f15e1` | Bootstrap smoke tests |
| Company, JobPosting, ScrapeRun data | PROJECT_SPEC §1, §2, §4 | 2-A | Models/migrations | Completed | `7d030e1` | Model, constraint, and migration tests |
| Missing fields remain null | PROJECT_SPEC §2 | 2-A | Normalization | Completed | `308eeb3` | Normalization tests |
| Stable content hash | PROJECT_SPEC §2, §4 | 2-A | Identity hashing | Completed | `308eeb3` | Deterministic hash tests |
| ID/fallback deduplication | PROJECT_SPEC §3, §4 | 2-A | Identity/persistence | Completed | `308eeb3`, `d261edf` | Identity upgrade and uniqueness tests |
| Create/update/last_seen | PROJECT_SPEC §4 | 2-A | Persistence | Completed | `d261edf` | Persistence tests |
| Run history and errors | PROJECT_SPEC §4, §7 | 2-A | ScrapeRun lifecycle | Planned | Not implemented | Lifecycle tests and fixture flow |
| Inactive after successful misses | PROJECT_SPEC §4 | 2-A | Reconciliation | Planned | Not implemented | Reconciliation tests |
| Failed run never deactivates jobs | AGENTS: Data rules; PROJECT_SPEC §7 | 2-A | Reconciliation | Planned | Not implemented | Partial/failed safety tests |
| One item failure does not abort run | PROJECT_SPEC §7 | 2-A | Recoverable pipeline | Planned | Not implemented | Fixture integration test |
| Scraping does not block UI | PROJECT_SPEC §6 | 2-A | Background execution | Planned | Not implemented | Background and manual gate |
| Production extraction/pagination | PROJECT_SPEC §2–3 | 2-B | Permitted adapter | Blocked | Source and approval missing | Approved-source evidence |
| Company management | PROJECT_SPEC §1, §5 | 3-A | Company UI | Planned | Depends on Track 2-A | Form/view/manual tests |
| Dashboard | PROJECT_SPEC §5 | 3-A | Dashboard | Planned | Depends on lifecycle | Query/view tests |
| Job list and filters | PROJECT_SPEC §5 | 3-A | Job list | Planned | Depends on stored jobs | Filter/view tests |
| Job detail | PROJECT_SPEC §5 | 3-A | Job detail | Planned | Depends on stored jobs | Template tests |
| Run controls and polling | PROJECT_SPEC §5–6 | 3-A | Run actions/status | Planned | Depends on background API | POST/HTMX tests |
| Final production flow | PROJECT_SPEC: Acceptance criteria | 3-B | Final acceptance | Blocked | Track 2-B incomplete | Approved end-to-end evidence |

## Open decisions

1. **Permitted production source:** the team must select a source and approve
   automated retrieval before Tracks 2-B or 3-B can proceed.
2. **Authentication/authorization:** `PROJECT_SPEC.md` defines no login, user,
   role, or permission model. None may be added without a separate requirement.

The completion boundaries of Tracks 2-A and 3-A are approved: each can become
`Completed` after its own gates, independently of blocked production tracks.
