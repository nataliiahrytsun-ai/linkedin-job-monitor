from __future__ import annotations

import importlib
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from scraping.background import (
    BackgroundRunAlreadyScheduledError,
    ControlledBackgroundExecutor,
)
from scraping.pipeline import (
    FixturePipelineError,
    FixturePipelineResult,
    run_fixture_pipeline,
)

FIXTURES = Path(__file__).parent / "fixtures" / "backend"


class IntegrationClock:
    def __init__(self) -> None:
        self._next = datetime(2026, 8, 9, 9, tzinfo=UTC)
        self._lock = threading.Lock()

    def __call__(self) -> datetime:
        with self._lock:
            value = self._next
            self._next += timedelta(seconds=1)
            return value


@pytest.fixture(scope="module", autouse=True)
def migrated_database(tmp_path_factory: pytest.TempPathFactory) -> None:
    apps = importlib.import_module("django.apps").apps
    if not apps.ready:
        database_path = tmp_path_factory.mktemp("backend-integration-db") / "backend.sqlite3"
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


def company(*, name: str = "Integration Company") -> Any:
    slug = name.lower().replace(" ", "-")
    company_record = model("companies.Company").objects.create(
        name=name,
        source="fixture",
        source_jobs_url=f"https://jobs.example.test/{slug}/openings",
    )
    model("companies.CompanySource").objects.create(
        company=company_record,
        source=company_record.source,
        source_jobs_url=company_record.source_jobs_url,
        approval_status="approved",
        is_active=True,
    )
    return company_record


def submit_and_wait(
    executor: ControlledBackgroundExecutor,
    company_record: Any,
    fixture_name: str,
    *,
    recover_job_errors: bool = True,
) -> FixturePipelineResult:
    handle = executor.submit_fixture_pipeline(
        company=company_record,
        fixture_path=FIXTURES / fixture_name,
        recover_job_errors=recover_job_errors,
    )
    return handle.future.result(timeout=10)


def jobs_for(company_record: Any) -> Any:
    return model("jobs.JobPosting").objects.filter(company=company_record)


def runs_for(company_record: Any) -> Any:
    return model("scrape_runs.ScrapeRun").objects.filter(company=company_record)


def job_by_source_id(company_record: Any, source_job_id: str) -> Any:
    return jobs_for(company_record).get(source_job_id=source_job_id)


def assert_terminal_company_state(company_record: Any, result: FixturePipelineResult) -> None:
    company_record.refresh_from_db()
    result.scrape_run.refresh_from_db()
    assert result.scrape_run.status != "running"
    assert runs_for(company_record).filter(status="running").count() == 0
    assert company_record.last_scrape_status == result.scrape_run.status
    assert company_record.last_scraped_at == result.scrape_run.finished_at
    assert result.scrape_run.requests_made == 0


def test_complete_backend_sequence_success_partial_and_failed() -> None:
    company_record = company()

    with ControlledBackgroundExecutor(clock=IntegrationClock()) as executor:
        run_1 = submit_and_wait(executor, company_record, "run_1.json")
        assert run_1.scrape_run.status == "success"
        assert run_1.jobs_found == 3
        assert run_1.jobs_created == 3
        assert run_1.jobs_updated == 0
        assert run_1.jobs_unchanged == 0
        assert run_1.jobs_failed == 0
        assert run_1.reconciliation is not None
        assert jobs_for(company_record).count() == 3
        assert_terminal_company_state(company_record, run_1)

        run_2 = submit_and_wait(executor, company_record, "run_2.json")
        assert run_2.scrape_run.status == "success"
        assert run_2.jobs_found == 3
        assert run_2.jobs_created == 1
        assert run_2.jobs_updated == 1
        assert run_2.jobs_unchanged == 1
        assert run_2.jobs_failed == 0
        assert run_2.reconciliation is not None
        assert jobs_for(company_record).count() == 4
        assert jobs_for(company_record).filter(
            source_job_id="fixture-python-developer"
        ).count() == 1
        analyst = job_by_source_id(company_record, "fixture-data-analyst")
        backend = job_by_source_id(company_record, "fixture-backend-developer")
        sales = job_by_source_id(company_record, "fixture-sales-manager")
        assert analyst.city == "Graz"
        assert backend.status == "active"
        assert sales.status == "active"
        assert sales.consecutive_successful_misses == 1
        assert_terminal_company_state(company_record, run_2)

        run_3 = submit_and_wait(executor, company_record, "run_3.json")
        assert run_3.scrape_run.status == "success"
        assert run_3.jobs_found == 3
        assert run_3.jobs_created == 0
        assert run_3.jobs_updated == 0
        assert run_3.jobs_unchanged == 3
        assert run_3.jobs_failed == 0
        assert run_3.reconciliation is not None
        sales.refresh_from_db()
        assert sales.status == "not_found"
        assert sales.consecutive_successful_misses == 2
        for source_job_id in (
            "fixture-python-developer",
            "fixture-data-analyst",
            "fixture-backend-developer",
        ):
            seen_job = job_by_source_id(company_record, source_job_id)
            assert seen_job.status == "active"
            assert seen_job.consecutive_successful_misses == 0
        assert jobs_for(company_record).count() == 4
        assert runs_for(company_record).count() == 3
        assert_terminal_company_state(company_record, run_3)

        partial = submit_and_wait(executor, company_record, "mixed_run.json")
        assert partial.scrape_run.status == "partial"
        assert partial.jobs_found == 3
        assert partial.jobs_created == 0
        assert partial.jobs_updated == 1
        assert partial.jobs_unchanged == 1
        assert partial.jobs_failed == 1
        assert partial.reconciliation is None
        assert len(partial.errors) == 1
        assert partial.errors[0].record_index == 1
        assert partial.errors[0].safe_message == (
            "Job record failed during normalization."
        )
        sales.refresh_from_db()
        backend.refresh_from_db()
        assert sales.status == "not_found"
        assert sales.consecutive_successful_misses == 2
        assert backend.status == "active"
        assert backend.consecutive_successful_misses == 0
        assert jobs_for(company_record).count() == 4
        assert_terminal_company_state(company_record, partial)

        state_before_failure = {
            job.pk: (
                job.status,
                job.consecutive_successful_misses,
                job.last_seen_at,
                job.content_hash,
            )
            for job in jobs_for(company_record)
        }
        failed_handle = executor.submit_fixture_pipeline(
            company=company_record,
            fixture_path=FIXTURES / "all_invalid_run.json",
            recover_job_errors=True,
        )
        with pytest.raises(FixturePipelineError) as caught:
            failed_handle.future.result(timeout=10)

        failed_run = caught.value.scrape_run
        failed_run.refresh_from_db()
        company_record.refresh_from_db()
        state_after_failure = {
            job.pk: (
                job.status,
                job.consecutive_successful_misses,
                job.last_seen_at,
                job.content_hash,
            )
            for job in jobs_for(company_record)
        }
        assert failed_run.status == "failed"
        assert failed_run.error_message
        assert "traceback" not in failed_run.error_message.lower()
        assert str(FIXTURES.resolve()).lower() not in failed_run.error_message.lower()
        assert failed_run.requests_made == 0
        assert state_after_failure == state_before_failure
        assert jobs_for(company_record).count() == 4
        assert runs_for(company_record).filter(status="running").count() == 0
        assert company_record.last_scrape_status == "failed"
        assert company_record.last_scraped_at == failed_run.finished_at

    histories = list(runs_for(company_record).order_by("started_at"))
    assert [run.status for run in histories] == [
        "success",
        "success",
        "success",
        "partial",
        "failed",
    ]
    assert {run.company_id for run in histories} == {company_record.pk}
    source_ids = list(jobs_for(company_record).values_list("source_job_id", flat=True))
    assert len(source_ids) == len(set(source_ids)) == 4


def test_company_isolation_and_shared_external_identities() -> None:
    first_company = company(name="First Integration Company")
    second_company = company(name="Second Integration Company")

    with ControlledBackgroundExecutor(
        max_workers=1,
        clock=IntegrationClock(),
    ) as executor:
        first_handle = executor.submit_fixture_pipeline(
            company=first_company,
            fixture_path=FIXTURES / "run_1.json",
        )
        second_handle = executor.submit_fixture_pipeline(
            company=second_company,
            fixture_path=FIXTURES / "run_1.json",
        )
        assert first_handle.task_id != second_handle.task_id
        first_result = first_handle.future.result(timeout=10)
        second_result = second_handle.future.result(timeout=10)
        assert first_result.scrape_run.status == "success"
        assert second_result.scrape_run.status == "success"

        second_snapshot = {
            job.source_job_id: (
                job.status,
                job.consecutive_successful_misses,
                job.content_hash,
            )
            for job in jobs_for(second_company)
        }
        first_update = submit_and_wait(executor, first_company, "run_2.json")
        assert first_update.scrape_run.status == "success"

    assert jobs_for(first_company).count() == 4
    assert jobs_for(second_company).count() == 3
    assert runs_for(first_company).count() == 2
    assert runs_for(second_company).count() == 1
    assert not runs_for(first_company).filter(company=second_company).exists()
    assert not runs_for(second_company).filter(company=first_company).exists()
    assert second_snapshot == {
        job.source_job_id: (
            job.status,
            job.consecutive_successful_misses,
            job.content_hash,
        )
        for job in jobs_for(second_company)
    }
    shared_source_id = "fixture-python-developer"
    first_python = job_by_source_id(first_company, shared_source_id)
    second_python = job_by_source_id(second_company, shared_source_id)
    assert first_python.pk != second_python.pk
    assert first_python.company_id == first_company.pk
    assert second_python.company_id == second_company.pk
    assert model("scrape_runs.ScrapeRun").objects.filter(status="running").count() == 0


def test_background_control_across_real_backend_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_company = company(name="Controlled First Company")
    second_company = company(name="Controlled Second Company")
    entered = threading.Event()
    release = threading.Event()
    first_call = True

    def held_real_pipeline(**kwargs: object) -> FixturePipelineResult:
        nonlocal first_call
        if first_call:
            first_call = False
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("integration test did not release pipeline")
        return run_fixture_pipeline(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(
        "scraping.background.run_fixture_pipeline",
        held_real_pipeline,
    )

    executor = ControlledBackgroundExecutor(
        max_workers=1,
        clock=IntegrationClock(),
    )
    try:
        first = executor.submit_fixture_pipeline(
            company=first_company,
            fixture_path=FIXTURES / "run_1.json",
        )
        assert entered.wait(timeout=5)
        assert first.future.done() is False
        with pytest.raises(BackgroundRunAlreadyScheduledError):
            executor.submit_fixture_pipeline(
                company=first_company,
                fixture_path=FIXTURES / "run_1.json",
            )

        other = executor.submit_fixture_pipeline(
            company=second_company,
            fixture_path=FIXTURES / "run_1.json",
        )
        assert other.future.done() is False
        release.set()
        assert first.future.result(timeout=10).scrape_run.status == "success"
        assert other.future.result(timeout=10).scrape_run.status == "success"

        after_success = submit_and_wait(
            executor,
            first_company,
            "run_1.json",
        )
        assert after_success.scrape_run.status == "success"

        failed = executor.submit_fixture_pipeline(
            company=first_company,
            fixture_path=FIXTURES / "all_invalid_run.json",
        )
        with pytest.raises(FixturePipelineError):
            failed.future.result(timeout=10)

        after_failure = submit_and_wait(
            executor,
            first_company,
            "run_1.json",
        )
        assert after_failure.scrape_run.status == "success"
        assert runs_for(first_company).filter(status="running").count() == 0
        assert runs_for(second_company).filter(status="running").count() == 0
    finally:
        release.set()
        executor.shutdown(wait=True)
