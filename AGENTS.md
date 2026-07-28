# AGENTS.md

## Project

Build an internal Django application that monitors publicly visible job postings
for customers and suppliers using Scrapling.

## Source of truth

- `docs/PROJECT_SPEC.md` contains the full requirements and acceptance criteria.
- `docs/SCRAPLING_GUIDE.md` contains verified Scrapling usage and project decisions.
- `docs/MILESTONES.md` defines the approved scope, gates, and completion
  criteria for each milestone.
- Read only the sections relevant to the current task.
- Read the complete specification when planning or validating a milestone.
- Do not silently change or expand the scope.

## Implementation order

1. Analyze the repository and relevant requirements.
2. Study Scrapling and create `docs/SCRAPLING_GUIDE.md`.
3. Run and document the Acuity Analytics technical spike.
4. Confirm which public LinkedIn data can be extracted reliably.
5. Implement models, migrations, extraction, normalization, and persistence.
6. Add controlled background execution.
7. Implement the Django Templates/HTMX UI.
8. Complete automated tests, manual checks, and documentation.

Do not build the full UI before the technical spike is completed.

## LinkedIn restrictions

Process only publicly accessible information.

Never implement:

- LinkedIn login automation;
- private accounts or authenticated cookies;
- CAPTCHA bypass or access-control circumvention;
- aggressive proxy rotation;
- scraping of personal profiles or non-public data;
- stealth features intended to bypass LinkedIn restrictions.

If public access is blocked, fail cleanly, record the limitation, and do not
implement a workaround.

## Scrapling

Scrapling is the required scraping framework.

- Test a normal HTTP fetcher first.
- Use asynchronous fetching only where it provides measurable value.
- Use a browser fetcher only when JavaScript rendering is required.
- Verify APIs against the installed Scrapling version and official docs.
- Centralize and document selectors.
- Record confirmed decisions in `docs/SCRAPLING_GUIDE.md`.
- Do not claim LinkedIn extraction works until the spike proves it.

## Architecture

Keep these concerns separate:

- fetching and crawling;
- selectors and extraction;
- normalization;
- persistence and status updates;
- background execution;
- Django views and templates.

Do not put scraping or persistence logic directly in views or templates.
Do not hard-code the implementation only for Acuity Analytics.

## Data and scraping rules

- Missing fields must be stored as `null` and must not fail a run.
- Deduplicate by LinkedIn Job ID, with a documented stable fallback.
- Repeated runs must create new jobs, update changed jobs, and refresh
  `last_seen_at`.
- A failed run must never mark jobs inactive.
- Use a stable content hash for change detection.
- Support public pagination or lazy loading where available.
- Enforce configurable request/page limits and prevent infinite loops.
- Use controlled concurrency, timeouts, bounded retries, delays, and session
  reuse where appropriate.
- One failed job detail page must not abort the company run.
- Prevent simultaneous duplicate runs for the same company.
- Scraping must not block the UI.

Prefer the smallest reliable background mechanism. Do not introduce Celery,
Redis, PostgreSQL, scheduling, or a separate frontend unless requested.

## Testing

- Do not add untested parsing logic.
- Use saved HTML fixtures for normal automated tests.
- Live LinkedIn tests must be marked separately and disabled by default.
- Run focused tests first, then the full available test suite.
- Run Ruff and configured type checking.
- Never report a test as passed unless it was actually executed successfully.

Cover at least extraction, missing fields, ID parsing, normalization,
pagination termination, deduplication, hashing, persistence, status updates,
error handling, URL validation, fixture-based scraping runs, UI display, and
filters.

## Repository hygiene

Do not commit:

- secrets or credentials;
- authenticated cookies;
- virtual environments;
- local databases;
- caches or browser-generated data;
- unnecessary complete LinkedIn HTML pages.

Use clear type hints, explicit exception handling, useful logs, and focused
modules. Do not silently swallow exceptions.

## Working procedure

Before editing:

1. Inspect the repository state.
2. Read the relevant requirements.
3. Identify the smallest coherent change.
4. State important assumptions.

After editing:

1. Review the diff.
2. Run applicable tests and quality checks.
3. Update affected documentation.
4. Report changed files, commands, results, unresolved risks, and manual checks.

Do not create commits, branches, pushes, or pull requests unless explicitly
requested.

Use `Pass`, `Fail`, or `Blocked` for manual checks. Do not mark a milestone
complete while required checks are failed or unreported.
