from __future__ import annotations

import importlib
import os
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scraping.persistence import PersistenceValidationError, persist_job_posting
from scraping.pipeline import (
    AllJobRecordsFailedError,
    FixturePipelineError,
    FixturePipelineResult,
    InvalidPipelineConfigurationError,
    run_fixture_pipeline,
)
from scraping.sources.fixture import FixtureFormatError

FIXTURES = Path(__file__).parent / "fixtures" / "backend"


@pytest.fixture(scope="module", autouse=True)
def migrated_database(tmp_path_factory: pytest.TempPathFactory) -> None:
    apps = importlib.import_module("django.apps").apps
    if not apps.ready:
        database_path = tmp_path_factory.mktemp("recoverable-db") / "recoverable.sqlite3"
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


def company(*, name: str = "Recoverable Fixture Company") -> Any:
    slug = name.lower().replace(" ", "-")
    return model("companies.Company").objects.create(
        name=name,
        source="fixture",
        source_jobs_url=f"https://jobs.example.test/{slug}/openings",
    )


def run_times(number: int) -> tuple[datetime, datetime]:
    started_at = datetime(2026, 8, 7, 9, tzinfo=UTC) + timedelta(hours=number)
    return started_at, started_at + timedelta(minutes=1)


def run_fixture(
    company_record: Any,
    fixture_name: str,
    *,
    number: int,
    recover: bool = True,
) -> FixturePipelineResult:
    started_at, finished_at = run_times(number)
    return run_fixture_pipeline(
        company=company_record,
        fixture_path=FIXTURES / fixture_name,
        started_at=started_at,
        finished_at=finished_at,
        recover_job_errors=recover,
    )


def jobs_for(company_record: Any) -> Any:
    return model("jobs.JobPosting").objects.filter(company=company_record)


def job_by_source_id(company_record: Any, source_job_id: str) -> Any:
    return jobs_for(company_record).get(source_job_id=source_job_id)


def test_recoverable_mode_keeps_success_and_reconciliation_compatible() -> None:
    company_record = company()

    result = run_fixture(company_record, "run_1.json", number=1)

    assert result.scrape_run.status == "success"
    assert result.scrape_run.error_message == ""
    assert result.jobs_found == 3
    assert result.jobs_created == 3
    assert result.jobs_updated == 0
    assert result.jobs_unchanged == 0
    assert result.jobs_failed == 0
    assert result.errors == ()
    assert result.reconciliation is not None
    assert result.reconciliation.seen_jobs == 3
    assert result.scrape_run.requests_made == 0


def test_mixed_fixture_commits_good_records_as_partial_without_reconciliation() -> None:
    company_record = company()

    result = run_fixture(company_record, "mixed_run.json", number=1)

    assert result.scrape_run.status == "partial"
    assert result.scrape_run.error_message == "1 of 3 job records failed processing"
    assert result.jobs_found == 3
    assert result.jobs_created == 2
    assert result.jobs_updated == 0
    assert result.jobs_unchanged == 0
    assert result.jobs_failed == 1
    accounted_records = (
        result.jobs_created
        + result.jobs_updated
        + result.jobs_unchanged
        + result.jobs_failed
    )
    assert accounted_records == result.jobs_found
    assert result.scrape_run.jobs_found == 3
    assert result.scrape_run.jobs_created == 2
    assert result.scrape_run.jobs_updated == 0
    assert result.scrape_run.requests_made == 0
    assert result.reconciliation is None
    assert jobs_for(company_record).count() == 2
    assert list(jobs_for(company_record).order_by("pk").values_list("title", flat=True)) == [
        "Python Developer",
        "Data Analyst",
    ]

    assert len(result.errors) == 1
    error = result.errors[0]
    assert error.record_index == 1
    assert error.stage == "normalization"
    assert error.safe_error_type == "ValueError"
    assert error.safe_message == "Job record failed during normalization."


def test_partial_update_preserves_absent_job_status_and_miss_counter() -> None:
    company_record = company()
    run_fixture(company_record, "run_1.json", number=1, recover=False)
    sales = job_by_source_id(company_record, "fixture-sales-manager")
    previous_sales_seen_at = sales.last_seen_at

    result = run_fixture(company_record, "mixed_run.json", number=2)

    company_record.refresh_from_db()
    sales.refresh_from_db()
    analyst = job_by_source_id(company_record, "fixture-data-analyst")
    python_job = job_by_source_id(company_record, "fixture-python-developer")
    assert result.scrape_run.status == "partial"
    assert result.jobs_created == 0
    assert result.jobs_updated == 1
    assert result.jobs_unchanged == 1
    assert result.jobs_failed == 1
    assert result.reconciliation is None
    assert analyst.city == "Linz"
    assert analyst.last_seen_at == run_times(2)[1]
    assert python_job.last_seen_at == run_times(2)[1]
    assert sales.status == "active"
    assert sales.consecutive_successful_misses == 0
    assert sales.last_seen_at == previous_sales_seen_at
    assert company_record.last_scrape_status == "partial"
    assert company_record.last_scraped_at == run_times(2)[1]


def test_all_invalid_fixture_finishes_failed_without_jobs_or_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = company()
    reconciliation_called = False

    def unexpected_reconciliation(**kwargs: object) -> Any:
        nonlocal reconciliation_called
        reconciliation_called = True
        raise AssertionError("reconciliation must not run")

    monkeypatch.setattr(
        "scraping.pipeline.reconcile_jobs_after_successful_run",
        unexpected_reconciliation,
    )

    with pytest.raises(FixturePipelineError) as caught:
        run_fixture(company_record, "all_invalid_run.json", number=1)

    assert isinstance(caught.value.__cause__, AllJobRecordsFailedError)
    failed_run = caught.value.scrape_run
    failed_run.refresh_from_db()
    company_record.refresh_from_db()
    assert failed_run.status == "failed"
    assert failed_run.error_message == "Fixture pipeline failed: AllJobRecordsFailedError"
    assert failed_run.jobs_found == 0
    assert failed_run.jobs_created == 0
    assert failed_run.jobs_updated == 0
    assert failed_run.requests_made == 0
    assert company_record.last_scrape_status == "failed"
    assert jobs_for(company_record).count() == 0
    assert reconciliation_called is False


def test_recoverable_persistence_error_rolls_back_only_its_record_savepoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = company()
    original_persist = persist_job_posting
    calls = 0

    def fail_after_second_save(**kwargs: object) -> Any:
        nonlocal calls
        calls += 1
        persisted = original_persist(**kwargs)  # type: ignore[arg-type]
        if calls == 2:
            raise PersistenceValidationError("synthetic recoverable save error")
        return persisted

    monkeypatch.setattr("scraping.pipeline.persist_job_posting", fail_after_second_save)

    result = run_fixture(company_record, "run_1.json", number=1)

    assert result.scrape_run.status == "partial"
    assert result.jobs_found == 3
    assert result.jobs_created == 2
    assert result.jobs_failed == 1
    assert result.reconciliation is None
    assert result.errors[0].record_index == 1
    assert result.errors[0].stage == "persistence"
    assert result.errors[0].safe_error_type == "PersistenceValidationError"
    assert list(jobs_for(company_record).order_by("pk").values_list("title", flat=True)) == [
        "Python Developer",
        "Sales Manager",
    ]


def test_unexpected_job_error_rolls_back_outer_batch_and_finishes_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = company()
    original_persist = persist_job_posting
    calls = 0

    def fail_unexpectedly_on_second(**kwargs: object) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic unexpected system error")
        return original_persist(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "scraping.pipeline.persist_job_posting", fail_unexpectedly_on_second
    )

    with pytest.raises(FixturePipelineError) as caught:
        run_fixture(company_record, "run_1.json", number=1)

    assert isinstance(caught.value.__cause__, RuntimeError)
    assert caught.value.scrape_run.status == "failed"
    assert caught.value.scrape_run.error_message == "Fixture pipeline failed: RuntimeError"
    assert jobs_for(company_record).count() == 0
    assert model("scrape_runs.ScrapeRun").objects.filter(status="partial").count() == 0


def test_invalid_json_remains_full_failure_in_recoverable_mode(tmp_path: Path) -> None:
    company_record = company()
    invalid_json = tmp_path / "confidential-name.json"
    invalid_json.write_text("not-json", encoding="utf-8")
    started_at, finished_at = run_times(1)

    with pytest.raises(FixturePipelineError) as caught:
        run_fixture_pipeline(
            company=company_record,
            fixture_path=invalid_json,
            started_at=started_at,
            finished_at=finished_at,
            recover_job_errors=True,
        )

    assert isinstance(caught.value.__cause__, FixtureFormatError)
    assert caught.value.scrape_run.status == "failed"
    assert caught.value.scrape_run.error_message == "Fixture pipeline failed: FixtureFormatError"
    assert str(invalid_json.resolve()) not in caught.value.scrape_run.error_message
    assert jobs_for(company_record).count() == 0


def test_safe_partial_error_contains_no_record_path_traceback_or_sql() -> None:
    company_record = company()

    result = run_fixture(company_record, "mixed_run.json", number=1)

    serialized_error = " ".join(
        (
            result.errors[0].safe_error_type,
            result.errors[0].safe_message,
            result.scrape_run.error_message,
        )
    ).lower()
    assert "damaged identity-free job" not in serialized_error
    assert str(FIXTURES.resolve()).lower() not in serialized_error
    assert "traceback" not in serialized_error
    assert "select " not in serialized_error
    assert "insert " not in serialized_error
    assert "cookie" not in serialized_error


def test_partial_run_does_not_change_other_company() -> None:
    target = company(name="Target Company")
    other = company(name="Other Company")
    foreign = model("jobs.JobPosting").objects.create(
        company=other,
        source="fixture",
        source_job_id="foreign-job",
        content_hash="a" * 64,
        dedupe_key="b" * 64,
        status="active",
        consecutive_successful_misses=1,
    )

    result = run_fixture(target, "mixed_run.json", number=1)

    foreign.refresh_from_db()
    assert result.scrape_run.status == "partial"
    assert foreign.status == "active"
    assert foreign.consecutive_successful_misses == 1


def test_recoverable_configuration_and_error_objects_are_typed_and_immutable() -> None:
    company_record = company()
    started_at, finished_at = run_times(1)

    with pytest.raises(InvalidPipelineConfigurationError, match="bool"):
        run_fixture_pipeline(
            company=company_record,
            fixture_path=FIXTURES / "mixed_run.json",
            started_at=started_at,
            finished_at=finished_at,
            recover_job_errors=1,  # type: ignore[arg-type]
        )
    assert model("scrape_runs.ScrapeRun").objects.count() == 0

    result = run_fixture(company_record, "mixed_run.json", number=1)
    with pytest.raises(FrozenInstanceError):
        result.jobs_failed = 0  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.errors[0].record_index = 99  # type: ignore[misc]
