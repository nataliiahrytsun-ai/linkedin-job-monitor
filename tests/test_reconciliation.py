from __future__ import annotations

import importlib
import os
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from scraping.reconciliation import (
    DEFAULT_SUCCESSFUL_MISS_THRESHOLD,
    InvalidMissThresholdError,
    InvalidReconciliationRunError,
    InvalidSeenJobError,
    ReconciliationResult,
    RunNotSavedError,
    reconcile_jobs_after_successful_run,
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database(tmp_path_factory: pytest.TempPathFactory) -> None:
    apps = importlib.import_module("django.apps").apps
    if not apps.ready:
        database_path = tmp_path_factory.mktemp("reconciliation-db") / "reconciliation.sqlite3"
        os.environ["DJANGO_SETTINGS_MODULE"] = "job_monitor.settings"
        os.environ["JOB_MONITOR_SQLITE_PATH"] = str(database_path)
        django = importlib.import_module("django")
        django.setup()
    importlib.import_module("django.core.management").call_command(
        "migrate", interactive=False, verbosity=0
    )


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    importlib.import_module("django.core.management").call_command(
        "flush", interactive=False, verbosity=0
    )


def model(name: str) -> Any:
    return importlib.import_module("django.apps").apps.get_model(name)


def company(*, name: str = "Example") -> Any:
    company_record = model("companies.Company").objects.create(name=name, source="feed")
    model("companies.CompanySource").objects.create(
        company=company_record,
        source="feed",
        approval_status="approved",
        is_active=True,
    )
    return company_record


def company_source(company_record: Any) -> Any:
    return company_record.sources.get(source="feed")


def terminal_run(
    company_record: Any,
    *,
    status: str = "success",
    source_record: Any | None = None,
) -> Any:
    finished_at = datetime(2026, 8, 6, 11, tzinfo=UTC)
    return model("scrape_runs.ScrapeRun").objects.create(
        company=company_record,
        company_source=source_record or company_source(company_record),
        started_at=finished_at - timedelta(seconds=2),
        finished_at=finished_at,
        status=status,
        duration_seconds=Decimal("2.000"),
        jobs_found=3,
        jobs_created=1,
        jobs_updated=1,
        requests_made=2,
        error_message="" if status == "success" else "incomplete",
    )


def job(
    company_record: Any,
    sequence: int,
    *,
    status: str = "active",
    misses: int = 0,
    source_record: Any | None = None,
) -> Any:
    resolved_source = source_record or company_source(company_record)
    return model("jobs.JobPosting").objects.create(
        company=company_record,
        company_source=resolved_source,
        source=resolved_source.source,
        source_job_id=f"job-{sequence}",
        title=f"Job {sequence}",
        description=f"Description {sequence}",
        content_hash=f"{sequence:064x}",
        dedupe_key=f"{sequence + 1000:064x}",
        status=status,
        consecutive_successful_misses=misses,
        first_seen_at=datetime(2026, 8, 1, 9, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 5, 9, tzinfo=UTC),
    )


def reconcile(run: Any, seen: list[int], *, threshold: int = 2) -> ReconciliationResult:
    return reconcile_jobs_after_successful_run(
        scrape_run=run,
        seen_job_posting_ids=seen,
        miss_threshold=threshold,
    )


def snapshot(job_record: Any) -> tuple[object, ...]:
    job_record.refresh_from_db()
    return (
        job_record.status,
        job_record.consecutive_successful_misses,
        job_record.last_seen_at,
        job_record.title,
        job_record.description,
        job_record.content_hash,
        job_record.dedupe_key,
        job_record.source,
        job_record.company_id,
    )


def test_default_threshold_and_immutable_result() -> None:
    assert DEFAULT_SUCCESSFUL_MISS_THRESHOLD == 2
    result = ReconciliationResult(0, 0, 0, 0, 0, 0, 0)
    with pytest.raises(FrozenInstanceError):
        result.total_source_jobs = 1  # type: ignore[misc]


@pytest.mark.parametrize("status", ["running", "partial", "failed"])
def test_only_finished_success_run_is_accepted_without_job_changes(status: str) -> None:
    company_record = company()
    job_record = job(company_record, 1, misses=1)
    if status == "running":
        run = model("scrape_runs.ScrapeRun").objects.create(
            company=company_record,
            company_source=company_source(company_record),
        )
    else:
        run = terminal_run(company_record, status=status)
    before = snapshot(job_record)

    with pytest.raises(InvalidReconciliationRunError, match="finished SUCCESS"):
        reconcile(run, [])

    assert snapshot(job_record) == before


def test_unsaved_run_is_rejected_without_job_changes() -> None:
    company_record = company()
    job_record = job(company_record, 1, misses=1)
    unsaved = model("scrape_runs.ScrapeRun")(
        company=company_record,
        company_source=company_source(company_record),
        status="success",
        finished_at=datetime(2026, 8, 6, 11, tzinfo=UTC),
    )
    before = snapshot(job_record)

    with pytest.raises(RunNotSavedError):
        reconcile(unsaved, [])

    assert snapshot(job_record) == before


@pytest.mark.parametrize("threshold", [0, -1, True, False, 1.5, "2", None])
def test_invalid_threshold_is_rejected_before_database_changes(threshold: object) -> None:
    company_record = company()
    job_record = job(company_record, 1)
    run = terminal_run(company_record)

    with pytest.raises(InvalidMissThresholdError):
        reconcile_jobs_after_successful_run(
            scrape_run=run,
            seen_job_posting_ids=[],
            miss_threshold=threshold,  # type: ignore[arg-type]
        )

    assert snapshot(job_record)[0:2] == ("active", 0)


@pytest.mark.parametrize("invalid_id", [True, False, "1", 1.5, None])
def test_non_integer_seen_pk_is_rejected(invalid_id: object) -> None:
    company_record = company()
    job_record = job(company_record, 1)
    run = terminal_run(company_record)

    with pytest.raises(InvalidSeenJobError, match="integer database PKs"):
        reconcile_jobs_after_successful_run(
            scrape_run=run,
            seen_job_posting_ids=[invalid_id],  # type: ignore[list-item]
        )

    assert snapshot(job_record)[0:2] == ("active", 0)


def test_missing_seen_pk_is_rejected_atomically() -> None:
    company_record = company()
    existing = job(company_record, 1, misses=1)
    run = terminal_run(company_record)

    with pytest.raises(InvalidSeenJobError, match="do not exist"):
        reconcile(run, [existing.pk, existing.pk + 999])

    assert snapshot(existing)[0:2] == ("active", 1)


def test_foreign_company_seen_pk_is_rejected_atomically() -> None:
    first_company = company(name="First")
    second_company = company(name="Second")
    local = job(first_company, 1, misses=1)
    foreign = job(second_company, 2)
    run = terminal_run(first_company)

    with pytest.raises(InvalidSeenJobError, match="another company"):
        reconcile(run, [local.pk, foreign.pk])

    assert snapshot(local)[0:2] == ("active", 1)
    assert snapshot(foreign)[0:2] == ("active", 0)


def test_seen_active_resets_counter_without_changing_content_or_seen_time() -> None:
    company_record = company()
    seen = job(company_record, 1, misses=2)
    before = snapshot(seen)

    result = reconcile(terminal_run(company_record), [seen.pk])

    after = snapshot(seen)
    assert after == ("active", 0, *before[2:])
    assert result == ReconciliationResult(1, 1, 0, 1, 0, 0, 0)


def test_seen_not_found_resets_counter_but_does_not_reopen() -> None:
    company_record = company()
    seen = job(company_record, 1, status="not_found", misses=2)

    result = reconcile(terminal_run(company_record), [seen.pk])

    assert snapshot(seen)[0:2] == ("not_found", 0)
    assert result.miss_counters_reset == 1
    assert result.jobs_marked_not_found == 0


def test_seen_closed_resets_stale_counter_but_preserves_closed_status() -> None:
    company_record = company()
    seen = job(company_record, 1, status="closed", misses=2)

    result = reconcile(terminal_run(company_record), [seen.pk])

    assert snapshot(seen)[0:2] == ("closed", 0)
    assert result.closed_jobs_unchanged == 1
    assert result.miss_counters_reset == 1


def test_first_successful_miss_keeps_active_job_active() -> None:
    company_record = company()
    unseen = job(company_record, 1)
    previous_seen_at = unseen.last_seen_at
    old_updated_at = datetime(2026, 8, 1, 8, tzinfo=UTC)
    model("jobs.JobPosting").objects.filter(pk=unseen.pk).update(updated_at=old_updated_at)

    result = reconcile(terminal_run(company_record), [])

    unseen.refresh_from_db()
    assert unseen.status == "active"
    assert unseen.consecutive_successful_misses == 1
    assert unseen.last_seen_at == previous_seen_at
    assert unseen.updated_at > old_updated_at
    assert result.miss_counters_incremented == 1
    assert result.jobs_marked_not_found == 0


def test_second_successful_miss_marks_active_job_not_found() -> None:
    company_record = company()
    unseen = job(company_record, 1, misses=1)
    model("jobs.JobPosting").objects.filter(pk=unseen.pk).update(
        last_reviewed_content_hash=unseen.content_hash
    )

    result = reconcile(terminal_run(company_record), [])

    assert snapshot(unseen)[0:2] == ("not_found", 2)
    unseen.refresh_from_db()
    assert unseen.last_reviewed_content_hash == unseen.content_hash
    assert result.jobs_marked_not_found == 1


def test_threshold_one_marks_unseen_active_job_not_found_immediately() -> None:
    company_record = company()
    unseen = job(company_record, 1)

    reconcile(terminal_run(company_record), [], threshold=1)

    assert snapshot(unseen)[0:2] == ("not_found", 1)


def test_threshold_three_requires_three_successful_misses() -> None:
    company_record = company()
    unseen = job(company_record, 1)

    reconcile(terminal_run(company_record), [], threshold=3)
    assert snapshot(unseen)[0:2] == ("active", 1)
    reconcile(terminal_run(company_record), [], threshold=3)
    assert snapshot(unseen)[0:2] == ("active", 2)
    reconcile(terminal_run(company_record), [], threshold=3)
    assert snapshot(unseen)[0:2] == ("not_found", 3)


def test_unseen_not_found_counter_is_capped_at_threshold() -> None:
    company_record = company()
    at_threshold = job(company_record, 1, status="not_found", misses=2)
    above_threshold = job(company_record, 2, status="not_found", misses=7)

    result = reconcile(terminal_run(company_record), [])

    assert snapshot(at_threshold)[0:2] == ("not_found", 2)
    assert snapshot(above_threshold)[0:2] == ("not_found", 2)
    assert result.jobs_marked_not_found == 0


def test_unseen_closed_job_and_counter_are_unchanged() -> None:
    company_record = company()
    closed = job(company_record, 1, status="closed", misses=4)
    before = snapshot(closed)
    previous_updated_at = closed.updated_at

    result = reconcile(terminal_run(company_record), [])

    assert snapshot(closed) == before
    closed.refresh_from_db()
    assert closed.updated_at == previous_updated_at
    assert result.closed_jobs_unchanged == 1
    assert result.miss_counters_incremented == 0


def test_duplicate_seen_ids_are_counted_once() -> None:
    company_record = company()
    seen = job(company_record, 1, misses=1)

    result = reconcile(terminal_run(company_record), [seen.pk, seen.pk, seen.pk])

    assert result.seen_jobs == 1
    assert result.miss_counters_reset == 1


def test_empty_seen_set_reconciles_all_company_jobs_and_result_counts_changes() -> None:
    company_record = company()
    job(company_record, 1)
    job(company_record, 2, misses=1)
    job(company_record, 3, status="closed", misses=3)

    result = reconcile(terminal_run(company_record), [])

    assert result == ReconciliationResult(
        total_source_jobs=3,
        seen_jobs=0,
        unseen_jobs=3,
        miss_counters_reset=0,
        miss_counters_incremented=2,
        jobs_marked_not_found=1,
        closed_jobs_unchanged=1,
    )


def test_other_company_jobs_are_never_changed() -> None:
    first_company = company(name="First")
    second_company = company(name="Second")
    local = job(first_company, 1)
    foreign = job(second_company, 1, misses=1)
    before = snapshot(foreign)

    result = reconcile(terminal_run(first_company), [])

    assert snapshot(local)[0:2] == ("active", 1)
    assert snapshot(foreign) == before
    assert result.total_company_jobs == 1


def test_reconciliation_is_scoped_to_the_run_company_source() -> None:
    company_record = company()
    first_source = company_source(company_record)
    second_source = model("companies.CompanySource").objects.create(
        company=company_record,
        source="lever",
        source_jobs_url="https://jobs.lever.co/example",
        approval_status="approved",
        is_active=True,
    )
    first_job = job(company_record, 1)
    second_job = model("jobs.JobPosting").objects.create(
        company=company_record,
        company_source=second_source,
        source="lever",
        source_job_id="job-1",
        title="Lever Job",
        content_hash="f" * 64,
        dedupe_key="e" * 64,
        status="active",
        consecutive_successful_misses=0,
        first_seen_at=datetime(2026, 8, 1, 9, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 5, 9, tzinfo=UTC),
    )
    run = terminal_run(company_record)
    assert run.company_source_id == first_source.pk

    result = reconcile(run, [], threshold=1)

    assert snapshot(first_job)[0:2] == ("not_found", 1)
    assert snapshot(second_job)[0:2] == ("active", 0)
    assert result.total_source_jobs == 1


def test_reconciliation_of_second_source_does_not_change_first_source() -> None:
    company_record = company()
    first_job = job(company_record, 1, misses=1)
    second_source = model("companies.CompanySource").objects.create(
        company=company_record,
        source="lever",
        source_jobs_url="https://jobs.lever.co/example",
        approval_status="approved",
        is_active=True,
    )
    second_job = job(
        company_record,
        2,
        source_record=second_source,
    )

    result = reconcile(
        terminal_run(company_record, source_record=second_source),
        [],
        threshold=1,
    )

    assert snapshot(first_job)[0:2] == ("active", 1)
    assert snapshot(second_job)[0:2] == ("not_found", 1)
    assert result.total_source_jobs == 1


def test_seen_job_from_another_source_of_same_company_is_rejected() -> None:
    company_record = company()
    local = job(company_record, 1, misses=1)
    second_source = model("companies.CompanySource").objects.create(
        company=company_record,
        source="lever",
        source_jobs_url="https://jobs.lever.co/example",
        approval_status="approved",
        is_active=True,
    )
    foreign = model("jobs.JobPosting").objects.create(
        company=company_record,
        company_source=second_source,
        source="lever",
        source_job_id="lever-job",
        title="Lever Job",
        content_hash="d" * 64,
        dedupe_key="c" * 64,
        first_seen_at=datetime(2026, 8, 1, 9, tzinfo=UTC),
        last_seen_at=datetime(2026, 8, 5, 9, tzinfo=UTC),
    )
    run = terminal_run(company_record)

    with pytest.raises(InvalidSeenJobError, match="another company source"):
        reconcile(run, [local.pk, foreign.pk])

    assert snapshot(local)[0:2] == ("active", 1)
    assert snapshot(foreign)[0:2] == ("active", 0)


def test_company_and_run_lifecycle_fields_are_not_changed() -> None:
    company_record = company()
    run = terminal_run(company_record)
    job(company_record, 1)
    company_record.refresh_from_db()
    run.refresh_from_db()
    company_values = (
        company_record.last_scrape_status,
        company_record.last_scraped_at,
    )
    run_values = (
        run.status,
        run.finished_at,
        run.duration_seconds,
        run.jobs_found,
        run.jobs_created,
        run.jobs_updated,
        run.requests_made,
        run.error_message,
    )

    reconcile(run, [])

    company_record.refresh_from_db()
    run.refresh_from_db()
    assert (company_record.last_scrape_status, company_record.last_scraped_at) == company_values
    assert (
        run.status,
        run.finished_at,
        run.duration_seconds,
        run.jobs_found,
        run.jobs_created,
        run.jobs_updated,
        run.requests_made,
        run.error_message,
    ) == run_values


def test_database_error_rolls_back_all_job_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    company_record = company()
    first = job(company_record, 1)
    second = job(company_record, 2, misses=1)
    run = terminal_run(company_record)
    queryset_type = importlib.import_module("django.db.models.query").QuerySet
    original_bulk_update = queryset_type.bulk_update

    def update_then_fail(
        queryset: Any,
        objects: list[Any],
        fields: tuple[str, ...],
        **kwargs: object,
    ) -> int:
        original_bulk_update(queryset, objects, fields, **kwargs)
        raise RuntimeError("synthetic database failure")

    monkeypatch.setattr(queryset_type, "bulk_update", update_then_fail)

    with pytest.raises(RuntimeError, match="synthetic database failure"):
        reconcile(run, [])

    assert snapshot(first)[0:2] == ("active", 0)
    assert snapshot(second)[0:2] == ("active", 1)
