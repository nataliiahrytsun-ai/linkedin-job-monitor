# Project goal

Create an internal Django application that gets company vacancies from a
permitted source, saves and updates them without creating duplicates, tracks
vacancies that disappear, and shows the results to users.

# Milestone 1 — Technical spike

**Status:** Completed

## Goal

Verify whether vacancies can be extracted technically and whether pagination
can be processed within a strictly limited diagnostic.

## Result

- Live extraction was technically verified.
- Limited pagination was technically verified.
- Complete collection of every vacancy was not proven.
- Production LinkedIn retrieval is not authorized.
- Production operation requires a source selected and approved by the team.

The diagnostic evidence is recorded in
[`docs/diagnostics/linkedin-pagination-2026-08-05.md`](diagnostics/linkedin-pagination-2026-08-05.md).

# Milestone 2 — Backend

- **Source-neutral backend:** Completed
- **Production source integration:** Blocked

## Goal

Build the application's internal logic independently of a specific vacancy
source.

## What is ready

- Django and SQLite setup.
- `Company`, `JobPosting`, and `ScrapeRun` models.
- Normalization of incoming vacancy data.
- Stable identity hashing, deduplication, and safe identity upgrades.
- Creation and updating of job postings.
- The complete `ScrapeRun` lifecycle.
- `SUCCESS`, `PARTIAL`, and `FAILED` outcomes.
- Reconciliation of vacancies missing from successful runs.
- Transition from `ACTIVE` to `NOT_FOUND` after two consecutive successful
  misses.
- A fixture-based fake source for offline testing.
- Recoverable errors for individual vacancy records.
- Controlled background execution with duplicate active-run protection.
- Integration tests for the complete backend process.

In simple terms, the source-neutral backend works end to end and is tested with
local synthetic fixtures without using the network. The repeatable verification
steps are documented in
[`docs/BACKEND_VERIFICATION.md`](BACKEND_VERIFICATION.md).

## Production blocker

Production source integration cannot be completed until the team selects and
approves a permitted vacancy source and its automated retrieval. The limited
LinkedIn diagnostic is technical evidence only and does not authorize
production LinkedIn retrieval.

# Milestone 3 — User interface

**Status:** Planned

## Goal

Let users manage companies, start vacancy checks, and view stored vacancies and
run results.

## Planned parts

- Company management.
- Vacancy list.
- Filters.
- Vacancy detail page.
- `ScrapeRun` history.
- Display of `SUCCESS`, `PARTIAL`, and `FAILED` states.
- A command in the UI to start a check.
- Display of the current run state.
- Dashboard.
- Mobile and narrow-screen verification.
- Final tests and documentation.

The UI can be developed and tested with the fixture-based backend. Final
production end-to-end verification remains blocked until a permitted source is
selected and approved.
