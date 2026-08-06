from __future__ import annotations

import importlib
import os
import socket
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scraping.persistence import persist_job_posting
from scraping.pipeline import (
    FixturePipelineError,
    FixturePipelineResult,
    InvalidPipelineTimestampError,
    run_fixture_pipeline,
)
from scraping.run_lifecycle import (
    DuplicateRunningRunError,
    InactiveCompanyError,
    start_scrape_run,
)
from scraping.sources.fixture import (
    FixtureFileNotFoundError,
    FixtureFormatError,
    load_fixture_records,
)

FIXTURES = Path(__file__).parent / "fixtures" / "backend"


@pytest.fixture(scope="module", autouse=True)
def migrated_database(tmp_path_factory: pytest.TempPathFactory) -> None:
    apps = importlib.import_module("django.apps").apps
    if not apps.ready:
        database_path = tmp_path_factory.mktemp("fixture-pipeline-db") / "pipeline.sqlite3"
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


def company(*, name: str = "Fictional Company", active: bool = True) -> Any:
    company_slug = name.lower().replace(" ", "-")
    return model("companies.Company").objects.create(
        name=name,
        source="fixture",
        source_jobs_url=f"https://jobs.example.test/{company_slug}/openings",
        is_active=active,
    )


def run_times(number: int) -> tuple[datetime, datetime]:
    started_at = datetime(2026, 8, 6, 9, tzinfo=UTC) + timedelta(hours=number)
    return started_at, started_at + timedelta(minutes=1)


def run_fixture(
    company_record: Any,
    fixture_name: str,
    *,
    number: int,
    miss_threshold: int = 2,
) -> FixturePipelineResult:
    started_at, finished_at = run_times(number)
    return run_fixture_pipeline(
        company=company_record,
        fixture_path=FIXTURES / fixture_name,
        started_at=started_at,
        finished_at=finished_at,
        miss_threshold=miss_threshold,
    )


def jobs_for(company_record: Any) -> Any:
    return model("jobs.JobPosting").objects.filter(company=company_record)


def job_by_source_id(company_record: Any, source_job_id: str) -> Any:
    return jobs_for(company_record).get(source_job_id=source_job_id)


def test_fixture_loader_preserves_valid_record_order() -> None:
    records = load_fixture_records(FIXTURES / "run_1.json")

    assert [record["title"] for record in records] == [
        "Python Developer",
        "Data Analyst",
        "Sales Manager",
    ]


def test_fixture_loader_accepts_empty_array(tmp_path: Path) -> None:
    fixture_path = tmp_path / "empty.json"
    fixture_path.write_text("[]", encoding="utf-8")

    assert load_fixture_records(fixture_path) == ()


def test_fixture_loader_rejects_invalid_json_and_non_array(tmp_path: Path) -> None:
    invalid_json = tmp_path / "invalid.json"
    invalid_json.write_text("not-json", encoding="utf-8")
    non_array = tmp_path / "object.json"
    non_array.write_text('{"title": "Wrong root"}', encoding="utf-8")

    with pytest.raises(FixtureFormatError, match="valid JSON"):
        load_fixture_records(invalid_json)
    with pytest.raises(FixtureFormatError, match="root"):
        load_fixture_records(non_array)


def test_fixture_loader_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FixtureFileNotFoundError, match="does not exist"):
        load_fixture_records(tmp_path / "missing.json")


def test_run_one_creates_three_jobs_and_success_run_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = company()

    def reject_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("fixture pipeline must not use the network")

    monkeypatch.setattr(socket, "create_connection", reject_network)
    result = run_fixture(company_record, "run_1.json", number=1)

    assert result.jobs_found == 3
    assert result.jobs_created == 3
    assert result.jobs_updated == 0
    assert result.jobs_unchanged == 0
    assert result.jobs_failed == 0
    assert result.errors == ()
    assert result.scrape_run.status == "success"
    assert result.scrape_run.jobs_found == 3
    assert result.scrape_run.jobs_created == 3
    assert result.scrape_run.jobs_updated == 0
    assert result.scrape_run.requests_made == 0
    assert jobs_for(company_record).count() == 3
    assert set(jobs_for(company_record).values_list("company_id", flat=True)) == {
        company_record.pk
    }
    assert result.reconciliation is not None
    assert result.reconciliation.seen_jobs == 3
    assert result.reconciliation.unseen_jobs == 0
    assert all(
        stored.last_seen_at == run_times(1)[1] for stored in jobs_for(company_record)
    )


def test_repeating_same_fixture_updates_seen_time_without_duplicates() -> None:
    company_record = company()
    first = run_fixture(company_record, "run_1.json", number=1)
    second = run_fixture(company_record, "run_1.json", number=2)

    assert first.jobs_created == 3
    assert second.jobs_found == 3
    assert second.jobs_created == 0
    assert second.jobs_updated == 0
    assert second.jobs_unchanged == 3
    assert jobs_for(company_record).count() == 3
    assert all(
        stored.last_seen_at == run_times(2)[1] for stored in jobs_for(company_record)
    )
    assert model("scrape_runs.ScrapeRun").objects.count() == 2


def test_run_two_updates_creates_and_records_first_sales_miss() -> None:
    company_record = company()
    run_fixture(company_record, "run_1.json", number=1)

    result = run_fixture(company_record, "run_2.json", number=2)

    assert result.jobs_found == 3
    assert result.jobs_created == 1
    assert result.jobs_updated == 1
    assert result.jobs_unchanged == 1
    assert result.scrape_run.jobs_found == 3
    assert result.scrape_run.jobs_created == 1
    assert result.scrape_run.jobs_updated == 1
    assert result.scrape_run.requests_made == 0
    assert jobs_for(company_record).count() == 4
    assert job_by_source_id(company_record, "fixture-python-developer").title == "Python Developer"
    analyst = job_by_source_id(company_record, "fixture-data-analyst")
    assert analyst.city == "Graz"
    assert analyst.location == "Graz, Austria"
    assert job_by_source_id(company_record, "fixture-backend-developer").status == "active"
    sales = job_by_source_id(company_record, "fixture-sales-manager")
    assert sales.status == "active"
    assert sales.consecutive_successful_misses == 1
    assert result.reconciliation is not None
    assert result.reconciliation.miss_counters_incremented == 1


def test_run_three_marks_sales_not_found_without_missing_seen_jobs() -> None:
    company_record = company()
    run_fixture(company_record, "run_1.json", number=1)
    run_fixture(company_record, "run_2.json", number=2)

    result = run_fixture(company_record, "run_3.json", number=3)

    sales = job_by_source_id(company_record, "fixture-sales-manager")
    assert sales.status == "not_found"
    assert sales.consecutive_successful_misses == 2
    assert result.reconciliation is not None
    assert result.reconciliation.jobs_marked_not_found == 1
    assert result.jobs_created == 0
    assert result.jobs_updated == 0
    assert result.jobs_unchanged == 3
    for source_job_id in (
        "fixture-python-developer",
        "fixture-data-analyst",
        "fixture-backend-developer",
    ):
        seen = job_by_source_id(company_record, source_job_id)
        assert seen.status == "active"
        assert seen.consecutive_successful_misses == 0


def test_closed_missing_job_keeps_explicit_status_and_counter() -> None:
    company_record = company()
    run_fixture(company_record, "run_1.json", number=1)
    sales = job_by_source_id(company_record, "fixture-sales-manager")
    sales.status = "closed"
    sales.consecutive_successful_misses = 0
    sales.save(update_fields=("status", "consecutive_successful_misses"))

    result = run_fixture(company_record, "run_2.json", number=2)

    sales.refresh_from_db()
    assert sales.status == "closed"
    assert sales.consecutive_successful_misses == 0
    assert result.reconciliation is not None
    assert result.reconciliation.closed_jobs_unchanged == 1


def test_invalid_fixture_rolls_back_batch_and_finishes_failed_safely() -> None:
    company_record = company()
    existing = model("jobs.JobPosting").objects.create(
        company=company_record,
        source="fixture",
        source_job_id="existing-job",
        content_hash="a" * 64,
        dedupe_key="b" * 64,
        status="active",
        consecutive_successful_misses=1,
    )

    with pytest.raises(FixturePipelineError) as caught:
        run_fixture(company_record, "invalid_run.json", number=1)

    assert isinstance(caught.value.__cause__, ValueError)
    failed_run = caught.value.scrape_run
    failed_run.refresh_from_db()
    company_record.refresh_from_db()
    existing.refresh_from_db()
    assert failed_run.status == "failed"
    assert failed_run.error_message == "Fixture pipeline failed: ValueError"
    assert failed_run.jobs_found == 0
    assert failed_run.jobs_created == 0
    assert failed_run.jobs_updated == 0
    assert failed_run.requests_made == 0
    assert company_record.last_scrape_status == "failed"
    assert existing.status == "active"
    assert existing.consecutive_successful_misses == 1
    assert jobs_for(company_record).count() == 1


def test_empty_success_fixture_is_valid_and_reconciles_absence(tmp_path: Path) -> None:
    company_record = company()
    run_fixture(company_record, "run_1.json", number=1)
    empty_fixture = tmp_path / "empty.json"
    empty_fixture.write_text("[]", encoding="utf-8")
    started_at, finished_at = run_times(2)

    result = run_fixture_pipeline(
        company=company_record,
        fixture_path=empty_fixture,
        started_at=started_at,
        finished_at=finished_at,
    )

    assert result.scrape_run.status == "success"
    assert result.jobs_found == 0
    assert result.jobs_created == 0
    assert result.jobs_updated == 0
    assert result.jobs_unchanged == 0
    assert result.reconciliation is not None
    assert result.reconciliation.unseen_jobs == 3
    counters = jobs_for(company_record).values_list(
        "consecutive_successful_misses", flat=True
    )
    assert set(counters) == {1}


def test_pipeline_does_not_change_other_company_jobs_or_configuration() -> None:
    target = company(name="Target")
    other = company(name="Other")
    foreign_job = model("jobs.JobPosting").objects.create(
        company=other,
        source="fixture",
        source_job_id="fixture-python-developer",
        content_hash="c" * 64,
        dedupe_key="d" * 64,
        consecutive_successful_misses=1,
    )
    target_values = (target.source, target.source_jobs_url, target.company_type)

    run_fixture(target, "run_1.json", number=1)

    target.refresh_from_db()
    foreign_job.refresh_from_db()
    assert (target.source, target.source_jobs_url, target.company_type) == target_values
    assert foreign_job.status == "active"
    assert foreign_job.consecutive_successful_misses == 1
    assert model("companies.Company").objects.count() == 2


def test_inactive_and_duplicate_running_company_use_lifecycle_rejections() -> None:
    inactive = company(name="Inactive", active=False)
    started_at, finished_at = run_times(1)
    with pytest.raises(InactiveCompanyError):
        run_fixture_pipeline(
            company=inactive,
            fixture_path=FIXTURES / "run_1.json",
            started_at=started_at,
            finished_at=finished_at,
        )

    active = company(name="Active")
    start_scrape_run(company=active, started_at=started_at)
    with pytest.raises(DuplicateRunningRunError):
        run_fixture_pipeline(
            company=active,
            fixture_path=FIXTURES / "run_1.json",
            started_at=started_at + timedelta(minutes=1),
            finished_at=finished_at + timedelta(minutes=1),
        )
    assert model("scrape_runs.ScrapeRun").objects.filter(status="running").count() == 1


def test_batch_error_before_success_rolls_back_prior_persistence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = company()
    original_persist = persist_job_posting
    calls = 0

    def fail_second_persistence(**kwargs: object) -> Any:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic persistence failure")
        return original_persist(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "scraping.pipeline.persist_job_posting", fail_second_persistence
    )

    with pytest.raises(FixturePipelineError) as caught:
        run_fixture(company_record, "run_1.json", number=1)

    assert isinstance(caught.value.__cause__, RuntimeError)
    assert jobs_for(company_record).count() == 0
    assert caught.value.scrape_run.status == "failed"
    assert model("scrape_runs.ScrapeRun").objects.filter(status="running").count() == 0


def test_reconciliation_error_rolls_back_batch_and_success_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = company()

    def fail_reconciliation(**kwargs: object) -> Any:
        raise RuntimeError("synthetic reconciliation failure")

    monkeypatch.setattr(
        "scraping.pipeline.reconcile_jobs_after_successful_run",
        fail_reconciliation,
    )

    with pytest.raises(FixturePipelineError) as caught:
        run_fixture(company_record, "run_1.json", number=1)

    run = model("scrape_runs.ScrapeRun").objects.get()
    company_record.refresh_from_db()
    assert isinstance(caught.value.__cause__, RuntimeError)
    assert jobs_for(company_record).count() == 0
    assert run.status == "failed"
    assert run.jobs_found == 0
    assert run.requests_made == 0
    assert company_record.last_scrape_status == "failed"


def test_invalid_timestamps_are_rejected_before_run_creation() -> None:
    company_record = company()
    aware_start, aware_finish = run_times(1)

    with pytest.raises(InvalidPipelineTimestampError, match="started_at"):
        run_fixture_pipeline(
            company=company_record,
            fixture_path=FIXTURES / "run_1.json",
            started_at=aware_start.replace(tzinfo=None),
            finished_at=aware_finish,
        )
    with pytest.raises(InvalidPipelineTimestampError, match="finished_at"):
        run_fixture_pipeline(
            company=company_record,
            fixture_path=FIXTURES / "run_1.json",
            started_at=aware_start,
            finished_at=aware_finish.replace(tzinfo=None),
        )
    with pytest.raises(InvalidPipelineTimestampError, match="earlier"):
        run_fixture_pipeline(
            company=company_record,
            fixture_path=FIXTURES / "run_1.json",
            started_at=aware_finish,
            finished_at=aware_start,
        )
    assert model("scrape_runs.ScrapeRun").objects.count() == 0


def test_pipeline_result_is_immutable() -> None:
    company_record = company()
    result = run_fixture(company_record, "run_1.json", number=1)

    with pytest.raises(FrozenInstanceError):
        result.jobs_found = 99  # type: ignore[misc]
