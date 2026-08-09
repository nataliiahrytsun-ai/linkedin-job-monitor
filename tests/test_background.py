from __future__ import annotations

import importlib
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from scraping.background import (
    BackgroundCompanyNotSavedError,
    BackgroundExecutorShutdownError,
    BackgroundRunAlreadyScheduledError,
    BackgroundSourceError,
    ControlledBackgroundExecutor,
    InvalidBackgroundClockError,
    InvalidMaxWorkersError,
    InvalidShutdownWaitError,
)
from scraping.pipeline import FixturePipelineError, FixturePipelineResult
from scraping.sources.base import SourceAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "backend"


class IncrementingClock:
    def __init__(self) -> None:
        self._next = datetime(2026, 8, 8, 9, tzinfo=UTC)
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
        database_path = tmp_path_factory.mktemp("background-db") / "background.sqlite3"
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


def company(*, name: str = "Background Company") -> Any:
    slug = name.lower().replace(" ", "-")
    return model("companies.Company").objects.create(
        name=name,
        source="fixture",
        source_jobs_url=f"https://jobs.example.test/{slug}/openings",
    )


def test_import_does_not_start_worker_thread() -> None:
    code = (
        "import threading; import scraping.background; "
        "assert [thread.name for thread in threading.enumerate()] == ['MainThread']"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=PROJECT_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


def test_default_and_explicit_worker_limits_are_accepted() -> None:
    default_executor = ControlledBackgroundExecutor()
    explicit_executor = ControlledBackgroundExecutor(max_workers=2)
    try:
        assert default_executor is not explicit_executor
    finally:
        default_executor.shutdown()
        explicit_executor.shutdown()


@pytest.mark.parametrize("max_workers", [0, -1, True, False, 1.5, "1", None])
def test_invalid_worker_limits_are_rejected(max_workers: object) -> None:
    with pytest.raises(InvalidMaxWorkersError):
        ControlledBackgroundExecutor(max_workers=max_workers)  # type: ignore[arg-type]


def test_invalid_clock_is_rejected() -> None:
    with pytest.raises(InvalidBackgroundClockError, match="callable"):
        ControlledBackgroundExecutor(clock=None)  # type: ignore[arg-type]


def test_submit_returns_before_worker_finishes_and_uses_another_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = company()
    entered = threading.Event()
    release = threading.Event()
    worker_thread_id: int | None = None
    sentinel = cast(FixturePipelineResult, object())

    def blocked_pipeline(**kwargs: object) -> FixturePipelineResult:
        nonlocal worker_thread_id
        worker_thread_id = threading.get_ident()
        entered.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test did not release worker")
        return sentinel

    monkeypatch.setattr("scraping.background.run_fixture_pipeline", blocked_pipeline)
    executor = ControlledBackgroundExecutor()
    try:
        handle = executor.submit_fixture_pipeline(
            company=company_record,
            fixture_path=FIXTURES / "run_1.json",
        )
        assert entered.wait(timeout=5)
        assert handle.task_id
        assert handle.company_id == company_record.pk
        assert handle.future.done() is False
        assert worker_thread_id != threading.get_ident()

        release.set()
        assert handle.future.result(timeout=5) is sentinel
    finally:
        release.set()
        executor.shutdown()


def test_worker_reloads_company_and_closes_thread_local_connections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted_company = company()
    main_thread_id = threading.get_ident()
    close_calls: list[int] = []
    worker_companies: list[Any] = []
    sentinel = cast(FixturePipelineResult, object())

    def record_close() -> None:
        close_calls.append(threading.get_ident())

    def inspect_pipeline(**kwargs: object) -> FixturePipelineResult:
        worker_companies.append(kwargs["company"])
        return sentinel

    monkeypatch.setattr("scraping.background.close_old_connections", record_close)
    monkeypatch.setattr("scraping.background.run_fixture_pipeline", inspect_pipeline)

    with ControlledBackgroundExecutor() as executor:
        result = executor.submit_fixture_pipeline(
            company=submitted_company,
            fixture_path=FIXTURES / "run_1.json",
        ).future.result(timeout=5)

    assert result is sentinel
    assert len(close_calls) == 2
    assert close_calls[0] == close_calls[1]
    assert close_calls[0] != main_thread_id
    assert worker_companies[0].pk == submitted_company.pk
    assert worker_companies[0] is not submitted_company


def test_source_neutral_submission_selects_adapter_from_reloaded_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted_company = company()
    selected_companies: list[Any] = []
    pipeline_calls: list[dict[str, object]] = []
    adapter = cast(SourceAdapter, object())
    sentinel = cast(FixturePipelineResult, object())

    def select_adapter(company_record: Any) -> SourceAdapter:
        selected_companies.append(company_record)
        return adapter

    def inspect_pipeline(**kwargs: object) -> FixturePipelineResult:
        pipeline_calls.append(kwargs)
        return sentinel

    monkeypatch.setattr("scraping.background.get_source_adapter", select_adapter)
    monkeypatch.setattr("scraping.background.run_source_pipeline", inspect_pipeline)

    with ControlledBackgroundExecutor() as executor:
        result = executor.submit_pipeline(company=submitted_company).future.result(
            timeout=5
        )

    assert result is sentinel
    assert len(selected_companies) == 2
    assert selected_companies[0] is submitted_company
    assert selected_companies[1].pk == submitted_company.pk
    assert selected_companies[1] is not submitted_company
    assert pipeline_calls[0]["company"] is selected_companies[1]
    assert pipeline_calls[0]["adapter"] is adapter


def test_source_neutral_submission_rejects_unknown_source() -> None:
    company_record = company()
    company_record.source = "unknown"
    company_record.save(update_fields=("source",))

    with (
        ControlledBackgroundExecutor() as executor,
        pytest.raises(BackgroundSourceError, match="unknown"),
    ):
        executor.submit_pipeline(company=company_record)

    assert model("scrape_runs.ScrapeRun").objects.count() == 0


def test_real_success_pipeline_uses_runtime_clock_and_returns_result() -> None:
    company_record = company()
    clock = IncrementingClock()

    with ControlledBackgroundExecutor(clock=clock) as executor:
        result = executor.submit_pipeline(company=company_record).future.result(timeout=10)

    result.scrape_run.refresh_from_db()
    stored_jobs = list(
        model("jobs.JobPosting").objects.filter(company=company_record).order_by("pk")
    )
    assert result.scrape_run.status == "success"
    assert result.scrape_run.requests_made == 0
    assert result.reconciliation is not None
    assert len(stored_jobs) == 3
    assert result.scrape_run.started_at == datetime(2026, 8, 8, 9, tzinfo=UTC)
    assert [job.last_seen_at for job in stored_jobs] == [
        datetime(2026, 8, 8, 9, 0, 1, tzinfo=UTC),
        datetime(2026, 8, 8, 9, 0, 2, tzinfo=UTC),
        datetime(2026, 8, 8, 9, 0, 3, tzinfo=UTC),
    ]
    assert result.scrape_run.finished_at == datetime(
        2026, 8, 8, 9, 0, 4, tzinfo=UTC
    )


def test_real_partial_pipeline_returns_partial_without_reconciliation() -> None:
    company_record = company()

    with ControlledBackgroundExecutor(clock=IncrementingClock()) as executor:
        result = executor.submit_fixture_pipeline(
            company=company_record,
            fixture_path=FIXTURES / "mixed_run.json",
            recover_job_errors=True,
        ).future.result(timeout=10)

    assert result.scrape_run.status == "partial"
    assert result.jobs_created == 2
    assert result.jobs_failed == 1
    assert result.reconciliation is None
    assert result.scrape_run.requests_made == 0
    assert model("jobs.JobPosting").objects.filter(company=company_record).count() == 2


def test_failed_future_keeps_pipeline_error_and_allows_resubmission() -> None:
    company_record = company()

    with ControlledBackgroundExecutor(clock=IncrementingClock()) as executor:
        failed = executor.submit_fixture_pipeline(
            company=company_record,
            fixture_path=FIXTURES / "all_invalid_run.json",
            recover_job_errors=True,
        )
        with pytest.raises(FixturePipelineError):
            failed.future.result(timeout=10)

        failed_run = model("scrape_runs.ScrapeRun").objects.get()
        assert failed_run.status == "failed"
        assert failed_run.requests_made == 0

        retry = executor.submit_fixture_pipeline(
            company=company_record,
            fixture_path=FIXTURES / "run_1.json",
        )
        assert retry.future.result(timeout=10).scrape_run.status == "success"


def test_error_before_pipeline_does_not_leave_company_active(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = company()
    sentinel = cast(FixturePipelineResult, object())
    executor = ControlledBackgroundExecutor()
    original_worker = executor._run_fixture_pipeline

    def fail_before_pipeline(**kwargs: object) -> FixturePipelineResult:
        raise LookupError("synthetic pre-pipeline failure")

    def successful_retry(**kwargs: object) -> FixturePipelineResult:
        return sentinel

    monkeypatch.setattr(executor, "_run_fixture_pipeline", fail_before_pipeline)
    try:
        failed = executor.submit_fixture_pipeline(
            company=company_record,
            fixture_path=FIXTURES / "run_1.json",
        )
        with pytest.raises(LookupError, match="pre-pipeline"):
            failed.future.result(timeout=5)

        monkeypatch.setattr(executor, "_run_fixture_pipeline", original_worker)
        monkeypatch.setattr(
            "scraping.background.run_fixture_pipeline",
            successful_retry,
        )
        retry = executor.submit_fixture_pipeline(
            company=company_record,
            fixture_path=FIXTURES / "run_1.json",
        )
        assert retry.future.result(timeout=5) is sentinel
    finally:
        executor.shutdown()


def test_duplicate_company_is_rejected_while_active_then_allowed_after_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = company()
    entered = threading.Event()
    release = threading.Event()
    sentinel = cast(FixturePipelineResult, object())

    def blocked_pipeline(**kwargs: object) -> FixturePipelineResult:
        entered.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test did not release worker")
        return sentinel

    monkeypatch.setattr("scraping.background.run_fixture_pipeline", blocked_pipeline)
    executor = ControlledBackgroundExecutor()
    try:
        first = executor.submit_fixture_pipeline(
            company=company_record,
            fixture_path=FIXTURES / "run_1.json",
        )
        assert entered.wait(timeout=5)
        with pytest.raises(BackgroundRunAlreadyScheduledError):
            executor.submit_fixture_pipeline(
                company=company_record,
                fixture_path=FIXTURES / "run_1.json",
            )

        release.set()
        assert first.future.result(timeout=5) is sentinel
        second = executor.submit_fixture_pipeline(
            company=company_record,
            fixture_path=FIXTURES / "run_1.json",
        )
        assert second.future.result(timeout=5) is sentinel
    finally:
        release.set()
        executor.shutdown()


def test_different_companies_can_be_queued_in_one_executor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first_company = company(name="First Background Company")
    second_company = company(name="Second Background Company")
    release = threading.Event()
    calls: list[int] = []
    sentinel = cast(FixturePipelineResult, object())

    def first_blocks(**kwargs: object) -> FixturePipelineResult:
        worker_company = cast(Any, kwargs["company"])
        calls.append(worker_company.pk)
        if len(calls) == 1 and not release.wait(timeout=5):
            raise TimeoutError("test did not release worker")
        return sentinel

    monkeypatch.setattr("scraping.background.run_fixture_pipeline", first_blocks)
    executor = ControlledBackgroundExecutor(max_workers=1)
    try:
        first = executor.submit_fixture_pipeline(
            company=first_company,
            fixture_path=FIXTURES / "run_1.json",
        )
        second = executor.submit_fixture_pipeline(
            company=second_company,
            fixture_path=FIXTURES / "run_1.json",
        )
        assert second.future.done() is False
        release.set()
        assert first.future.result(timeout=5) is sentinel
        assert second.future.result(timeout=5) is sentinel
        assert calls == [first_company.pk, second_company.pk]
    finally:
        release.set()
        executor.shutdown()


def test_unsaved_company_is_rejected_before_worker_submission() -> None:
    unsaved = model("companies.Company")(name="Unsaved", source="fixture")

    with (
        ControlledBackgroundExecutor() as executor,
        pytest.raises(BackgroundCompanyNotSavedError),
    ):
        executor.submit_fixture_pipeline(
            company=unsaved,
            fixture_path=FIXTURES / "run_1.json",
        )
    assert model("scrape_runs.ScrapeRun").objects.count() == 0


def test_shutdown_waits_is_repeatable_and_rejects_new_submissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = company()
    entered = threading.Event()
    release = threading.Event()
    shutdown_complete = threading.Event()
    sentinel = cast(FixturePipelineResult, object())

    def blocked_pipeline(**kwargs: object) -> FixturePipelineResult:
        entered.set()
        if not release.wait(timeout=5):
            raise TimeoutError("test did not release worker")
        return sentinel

    monkeypatch.setattr("scraping.background.run_fixture_pipeline", blocked_pipeline)
    executor = ControlledBackgroundExecutor()
    handle = executor.submit_fixture_pipeline(
        company=company_record,
        fixture_path=FIXTURES / "run_1.json",
    )
    assert entered.wait(timeout=5)

    def shut_down() -> None:
        executor.shutdown(wait=True)
        shutdown_complete.set()

    shutdown_thread = threading.Thread(target=shut_down, name="test-shutdown")
    shutdown_thread.start()
    try:
        assert shutdown_complete.wait(timeout=0.05) is False
        release.set()
        shutdown_thread.join(timeout=5)
        assert shutdown_complete.is_set()
        assert handle.future.result(timeout=5) is sentinel
        executor.shutdown(wait=True)
        with pytest.raises(BackgroundExecutorShutdownError):
            executor.submit_fixture_pipeline(
                company=company_record,
                fixture_path=FIXTURES / "run_1.json",
            )
        with pytest.raises(InvalidShutdownWaitError):
            executor.shutdown(wait=1)  # type: ignore[arg-type]
        assert not any(
            thread.name.startswith("fixture-pipeline")
            for thread in threading.enumerate()
        )
    finally:
        release.set()
        shutdown_thread.join(timeout=5)
        executor.shutdown(wait=True)
