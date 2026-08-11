from __future__ import annotations

import importlib
import os
from datetime import UTC, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import pytest

from scraping.run_lifecycle import (
    CompanyNotSavedError,
    DuplicateRunningRunError,
    InactiveCompanyError,
    InactiveCompanySourceError,
    InvalidRunCountersError,
    InvalidRunErrorMessageError,
    InvalidRunTimestampError,
    InvalidRunTransitionError,
    RunNotSavedError,
    TerminalRunStatus,
    finish_scrape_run,
    start_scrape_run,
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database(tmp_path_factory: pytest.TempPathFactory) -> None:
    apps = importlib.import_module("django.apps").apps
    if not apps.ready:
        database_path = tmp_path_factory.mktemp("lifecycle-db") / "lifecycle.sqlite3"
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


def company(*, name: str = "Example", active: bool = True) -> Any:
    company_record = model("companies.Company").objects.create(
        name=name,
        source="feed",
        is_active=active,
    )
    model("companies.CompanySource").objects.create(
        company=company_record,
        source="feed",
        approval_status="approved",
        is_active=True,
    )
    return company_record


def started_at() -> datetime:
    return datetime(2026, 8, 6, 10, tzinfo=UTC)


def finish(
    run: Any,
    *,
    status: TerminalRunStatus | str = TerminalRunStatus.SUCCESS,
    finished_at: datetime | None = None,
    jobs_found: int = 5,
    jobs_created: int = 2,
    jobs_updated: int = 1,
    requests_made: int = 3,
    error_message: str = "",
) -> Any:
    return finish_scrape_run(
        scrape_run=run,
        status=status,
        finished_at=finished_at or started_at() + timedelta(seconds=5),
        jobs_found=jobs_found,
        jobs_created=jobs_created,
        jobs_updated=jobs_updated,
        requests_made=requests_made,
        error_message=error_message,
    )


def create_job(company_record: Any, *, status: str, misses: int) -> Any:
    return model("jobs.JobPosting").objects.create(
        company=company_record,
        company_source=company_record.sources.get(),
        source="feed",
        source_job_id=f"job-{status}",
        content_hash="a" * 64,
        dedupe_key=(status[0] * 64),
        status=status,
        consecutive_successful_misses=misses,
    )


def test_start_creates_default_running_run_and_updates_only_company_status() -> None:
    company_record = company()
    previous_completion = started_at() - timedelta(days=1)
    company_record.last_scraped_at = previous_completion
    company_record.save(update_fields=("last_scraped_at",))

    run = start_scrape_run(company=company_record, started_at=started_at())
    company_record.refresh_from_db()

    assert run.status == "running"
    assert run.company_source_id == company_record.sources.get().pk
    assert run.started_at == started_at()
    assert run.finished_at is None
    assert run.duration_seconds is None
    assert run.jobs_found == 0
    assert run.jobs_created == 0
    assert run.jobs_updated == 0
    assert run.requests_made == 0
    assert run.error_message == ""
    assert company_record.last_scrape_status == "running"
    assert company_record.last_scraped_at == previous_completion


def test_duplicate_running_run_is_rejected_by_database_constraint() -> None:
    company_record = company()
    first = start_scrape_run(company=company_record, started_at=started_at())

    with pytest.raises(DuplicateRunningRunError, match="already has"):
        start_scrape_run(
            company=company_record,
            started_at=started_at() + timedelta(seconds=1),
        )

    assert model("scrape_runs.ScrapeRun").objects.count() == 1
    assert model("scrape_runs.ScrapeRun").objects.get().pk == first.pk


def test_different_companies_can_run_at_the_same_time() -> None:
    first = start_scrape_run(company=company(name="One"), started_at=started_at())
    second = start_scrape_run(company=company(name="Two"), started_at=started_at())

    assert first.pk != second.pk
    assert model("scrape_runs.ScrapeRun").objects.filter(status="running").count() == 2


def test_inactive_unsaved_and_naive_start_inputs_are_rejected() -> None:
    inactive = company(active=False)
    unsaved = model("companies.Company")(name="Unsaved", source="feed")

    with pytest.raises(InactiveCompanyError):
        start_scrape_run(company=inactive, started_at=started_at())
    with pytest.raises(CompanyNotSavedError):
        start_scrape_run(company=unsaved, started_at=started_at())
    with pytest.raises(InvalidRunTimestampError, match="timezone-aware"):
        start_scrape_run(company=company(name="Naive"), started_at=datetime(2026, 8, 6))

    assert model("scrape_runs.ScrapeRun").objects.count() == 0
    inactive.refresh_from_db()
    assert inactive.last_scrape_status == "never"


def test_inactive_company_source_is_rejected() -> None:
    company_record = company()
    source = company_record.sources.get()
    source.is_active = False
    source.save(update_fields=("is_active", "updated_at"))

    with pytest.raises(InactiveCompanySourceError):
        start_scrape_run(company_source=source, started_at=started_at())

    assert model("scrape_runs.ScrapeRun").objects.count() == 0


def test_scrape_run_rejects_company_source_from_another_company() -> None:
    first_company = company(name="First")
    second_company = company(name="Second")

    with pytest.raises(
        importlib.import_module("django.core.exceptions").ValidationError,
        match="belong to the run company",
    ):
        model("scrape_runs.ScrapeRun").objects.create(
            company=first_company,
            company_source=second_company.sources.get(),
        )

    assert model("scrape_runs.ScrapeRun").objects.count() == 0


def test_aware_non_utc_started_at_is_accepted() -> None:
    aware = datetime(2026, 8, 6, 12, tzinfo=timezone(timedelta(hours=2)))

    run = start_scrape_run(company=company(), started_at=aware)

    assert run.started_at == aware


def test_success_completion_sets_duration_counters_and_company() -> None:
    company_record = company()
    run = start_scrape_run(company=company_record, started_at=started_at())
    finished_at = started_at() + timedelta(seconds=5, microseconds=234_500)

    completed = finish(
        run,
        finished_at=finished_at,
        jobs_found=8,
        jobs_created=3,
        jobs_updated=2,
        requests_made=4,
        error_message=" ignored on success ",
    )
    company_record.refresh_from_db()

    assert completed.status == "success"
    assert completed.finished_at == finished_at
    assert completed.duration_seconds == Decimal("5.235")
    assert completed.jobs_found == 8
    assert completed.jobs_created == 3
    assert completed.jobs_updated == 2
    assert completed.requests_made == 4
    assert completed.error_message == ""
    assert company_record.last_scrape_status == "success"
    assert company_record.last_scraped_at == finished_at


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (TerminalRunStatus.PARTIAL, "  one recoverable item failed  "),
        (TerminalRunStatus.FAILED, "  source unavailable  "),
    ],
)
def test_partial_and_failed_completion_store_explanation_and_company_status(
    status: TerminalRunStatus,
    message: str,
) -> None:
    company_record = company()
    run = start_scrape_run(company=company_record, started_at=started_at())

    completed = finish(run, status=status, error_message=message)
    company_record.refresh_from_db()

    assert completed.status == status.value
    assert completed.error_message == message.strip()
    assert completed.jobs_found == 5
    assert company_record.last_scrape_status == status.value
    assert company_record.last_scraped_at == completed.finished_at


@pytest.mark.parametrize("status", [TerminalRunStatus.PARTIAL, TerminalRunStatus.FAILED])
def test_partial_and_failed_require_an_error_message(status: TerminalRunStatus) -> None:
    company_record = company()
    run = start_scrape_run(company=company_record, started_at=started_at())

    with pytest.raises(InvalidRunErrorMessageError, match="requires"):
        finish(run, status=status, error_message="  ")

    run.refresh_from_db()
    company_record.refresh_from_db()
    assert run.status == "running"
    assert company_record.last_scrape_status == "running"


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"jobs_found": -1}, "jobs_found"),
        ({"jobs_found": True}, "jobs_found"),
        ({"jobs_found": 1, "jobs_created": 2}, "jobs_created"),
        ({"jobs_found": 1, "jobs_updated": 2}, "jobs_updated"),
        ({"requests_made": False}, "requests_made"),
    ],
)
def test_invalid_counters_are_rejected_without_changes(
    overrides: dict[str, object],
    message: str,
) -> None:
    company_record = company()
    run = start_scrape_run(company=company_record, started_at=started_at())

    values: dict[str, object] = {
        "scrape_run": run,
        "status": TerminalRunStatus.SUCCESS,
        "finished_at": started_at() + timedelta(seconds=1),
        "jobs_found": 1,
        "jobs_created": 0,
        "jobs_updated": 0,
        "requests_made": 1,
    }
    values.update(overrides)
    with pytest.raises(InvalidRunCountersError, match=message):
        finish_scrape_run(**values)  # type: ignore[arg-type]

    run.refresh_from_db()
    company_record.refresh_from_db()
    assert run.status == "running"
    assert run.jobs_found == 0
    assert company_record.last_scrape_status == "running"


def test_invalid_finish_timestamps_are_rejected_without_changes() -> None:
    company_record = company()
    run = start_scrape_run(company=company_record, started_at=started_at())

    with pytest.raises(InvalidRunTimestampError, match="timezone-aware"):
        finish(run, finished_at=datetime(2026, 8, 6, 11))
    with pytest.raises(InvalidRunTimestampError, match="earlier"):
        finish(run, finished_at=started_at() - timedelta(microseconds=1))

    run.refresh_from_db()
    company_record.refresh_from_db()
    assert run.status == "running"
    assert run.finished_at is None
    assert company_record.last_scrape_status == "running"


def test_running_or_unknown_terminal_status_is_rejected() -> None:
    run = start_scrape_run(company=company(), started_at=started_at())

    with pytest.raises(InvalidRunTransitionError, match="terminal status"):
        finish(run, status="running")
    with pytest.raises(InvalidRunTransitionError, match="terminal status"):
        finish(run, status="cancelled")


def test_terminal_run_cannot_be_finished_again() -> None:
    run = start_scrape_run(company=company(), started_at=started_at())
    finish(run)

    with pytest.raises(InvalidRunTransitionError, match="only a RUNNING"):
        finish(run, status=TerminalRunStatus.FAILED, error_message="late failure")

    stored = model("scrape_runs.ScrapeRun").objects.get(pk=run.pk)
    assert stored.status == "success"


def test_unsaved_run_is_rejected() -> None:
    unsaved = model("scrape_runs.ScrapeRun")(
        company=company(),
        started_at=started_at(),
    )

    with pytest.raises(RunNotSavedError):
        finish(unsaved)


@pytest.mark.parametrize(
    ("status", "message"),
    [
        (TerminalRunStatus.SUCCESS, ""),
        (TerminalRunStatus.PARTIAL, "incomplete result"),
        (TerminalRunStatus.FAILED, "source failure"),
    ],
)
def test_new_run_after_terminal_preserves_last_completion(
    status: TerminalRunStatus,
    message: str,
) -> None:
    company_record = company()
    first = start_scrape_run(company=company_record, started_at=started_at())
    terminal = finish(first, status=status, error_message=message)
    previous_finished_at = terminal.finished_at

    second = start_scrape_run(
        company=company_record,
        started_at=started_at() + timedelta(hours=1),
    )
    company_record.refresh_from_db()

    assert second.status == "running"
    assert second.pk != first.pk
    assert company_record.last_scrape_status == "running"
    assert company_record.last_scraped_at == previous_finished_at
    assert model("scrape_runs.ScrapeRun").objects.count() == 2


@pytest.mark.parametrize(
    ("terminal_status", "message"),
    [
        (TerminalRunStatus.SUCCESS, ""),
        (TerminalRunStatus.PARTIAL, "incomplete"),
        (TerminalRunStatus.FAILED, "failed"),
    ],
)
def test_lifecycle_never_changes_or_deletes_job_postings(
    terminal_status: TerminalRunStatus,
    message: str,
) -> None:
    company_record = company()
    active = create_job(company_record, status="active", misses=2)
    closed = create_job(company_record, status="closed", misses=3)
    run = start_scrape_run(company=company_record, started_at=started_at())

    finish(run, status=terminal_status, error_message=message)

    active.refresh_from_db()
    closed.refresh_from_db()
    assert (active.status, active.consecutive_successful_misses) == ("active", 2)
    assert (closed.status, closed.consecutive_successful_misses) == ("closed", 3)
    assert model("jobs.JobPosting").objects.count() == 2


def test_start_rolls_back_run_when_company_update_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    company_record = company()
    company_model = model("companies.Company")
    original_save = company_model.save

    def failing_save(instance: Any, *args: object, **kwargs: object) -> None:
        if kwargs.get("update_fields") == ("last_scrape_status",):
            raise RuntimeError("synthetic company update failure")
        original_save(instance, *args, **kwargs)

    monkeypatch.setattr(company_model, "save", failing_save)

    with pytest.raises(RuntimeError, match="synthetic"):
        start_scrape_run(company=company_record, started_at=started_at())

    assert model("scrape_runs.ScrapeRun").objects.count() == 0
    company_record.refresh_from_db()
    assert company_record.last_scrape_status == "never"


def test_finish_rolls_back_run_when_company_update_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = company()
    run = start_scrape_run(company=company_record, started_at=started_at())
    company_model = model("companies.Company")
    original_save = company_model.save

    def failing_save(instance: Any, *args: object, **kwargs: object) -> None:
        if kwargs.get("update_fields") == ("last_scrape_status", "last_scraped_at"):
            raise RuntimeError("synthetic company finish failure")
        original_save(instance, *args, **kwargs)

    monkeypatch.setattr(company_model, "save", failing_save)

    with pytest.raises(RuntimeError, match="synthetic"):
        finish(run)

    run.refresh_from_db()
    company_record.refresh_from_db()
    assert run.status == "running"
    assert run.finished_at is None
    assert company_record.last_scrape_status == "running"
    assert company_record.last_scraped_at is None
