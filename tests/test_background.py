from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
import threading
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import pytest

from scraping.background import (
    BackgroundCompanyNotSavedError,
    BackgroundExecutorShutdownError,
    BackgroundInactiveCompanyError,
    BackgroundNoExecutableSourcesError,
    BackgroundRunAlreadyScheduledError,
    BackgroundSourceError,
    ControlledBackgroundExecutor,
    InvalidBackgroundClockError,
    InvalidMaxWorkersError,
    InvalidShutdownWaitError,
)
from scraping.pipeline import FixturePipelineError, FixturePipelineResult
from scraping.sources.base import SourceAdapter, SourceBatch, SourceError
from scraping.sources.darwinbox import DarwinboxMethod, DarwinboxSourceAdapter
from scraping.sources.lever import LeverSourceAdapter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).parent / "fixtures" / "backend"
LEVER_FIXTURES = Path(__file__).parent / "fixtures" / "lever"
DARWINBOX_FIXTURES = Path(__file__).parent / "fixtures" / "darwinbox"


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
    company_record = model("companies.Company").objects.create(
        name=name,
        source="fixture",
        source_jobs_url=f"https://jobs.example.test/{slug}/openings",
    )
    add_company_source(company_record)
    return company_record


def add_company_source(
    company_record: Any,
    *,
    source: str | None = None,
    source_jobs_url: str | None = None,
    approval_status: str = "approved",
    is_active: bool = True,
) -> Any:
    return model("companies.CompanySource").objects.create(
        company=company_record,
        source=source or company_record.source,
        source_jobs_url=(
            company_record.source_jobs_url if source_jobs_url is None else source_jobs_url
        ),
        approval_status=approval_status,
        is_active=is_active,
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
    worker_sources: list[Any] = []
    sentinel = cast(FixturePipelineResult, object())

    def record_close() -> None:
        close_calls.append(threading.get_ident())

    def inspect_pipeline(**kwargs: object) -> FixturePipelineResult:
        worker_sources.append(kwargs["company_source"])
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
    assert worker_sources[0].company_id == submitted_company.pk
    assert worker_sources[0].pk == submitted_company.sources.get().pk


def test_source_neutral_submission_selects_adapter_from_reloaded_company(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    submitted_company = company()
    selected_sources: list[Any] = []
    pipeline_calls: list[dict[str, object]] = []
    adapter = cast(SourceAdapter, object())
    sentinel = cast(FixturePipelineResult, object())

    def select_adapter(company_source: Any) -> SourceAdapter:
        selected_sources.append(company_source)
        return adapter

    def inspect_pipeline(**kwargs: object) -> FixturePipelineResult:
        pipeline_calls.append(kwargs)
        return sentinel

    monkeypatch.setattr("scraping.background.get_source_adapter", select_adapter)
    monkeypatch.setattr("scraping.background.run_source_pipeline", inspect_pipeline)

    with ControlledBackgroundExecutor() as executor:
        result = executor.submit_pipeline(company=submitted_company).future.result(timeout=5)

    assert result is sentinel
    assert len(selected_sources) == 2
    assert selected_sources[0].pk == submitted_company.sources.get().pk
    assert selected_sources[1].pk == selected_sources[0].pk
    assert selected_sources[1] is not selected_sources[0]
    assert pipeline_calls[0]["company_source"] is selected_sources[1]
    assert pipeline_calls[0]["adapter"] is adapter


def test_source_neutral_submission_rejects_unknown_source() -> None:
    company_record = company()
    company_record.source = "unknown"
    company_record.save(update_fields=("source",))
    company_source = company_record.sources.get()
    company_source.source = "unknown"
    company_source.save(update_fields=("source", "updated_at"))

    with (
        ControlledBackgroundExecutor() as executor,
        pytest.raises(BackgroundSourceError, match="unknown"),
    ):
        executor.submit_pipeline(company=company_record)

    assert model("scrape_runs.ScrapeRun").objects.count() == 0


def test_legacy_submission_without_executable_source_fails_closed() -> None:
    company_record = company()
    company_record.sources.update(is_active=False)

    with (
        ControlledBackgroundExecutor() as executor,
        pytest.raises(BackgroundSourceError, match="exactly one"),
    ):
        executor.submit_pipeline(company=company_record)

    assert model("scrape_runs.ScrapeRun").objects.count() == 0


def test_legacy_submission_with_two_executable_sources_fails_closed() -> None:
    company_record = company()
    model("companies.CompanySource").objects.create(
        company=company_record,
        source="lever",
        source_jobs_url="https://jobs.lever.co/example",
        approval_status="approved",
        is_active=True,
    )

    with (
        ControlledBackgroundExecutor() as executor,
        pytest.raises(BackgroundSourceError, match="exactly one"),
    ):
        executor.submit_pipeline(company=company_record)

    assert model("scrape_runs.ScrapeRun").objects.count() == 0


def test_lever_submission_uses_registry_and_company_site_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = model("companies.Company").objects.create(
        name="Olo",
        source="lever",
        source_jobs_url="https://jobs.lever.co/olo",
    )
    add_company_source(company_record)
    requested_urls: list[str] = []

    def fake_http_get(url: str, timeout_seconds: float) -> str:
        requested_urls.append(url)
        assert timeout_seconds > 0
        return "[]"

    registry = importlib.import_module("scraping.sources.registry")
    monkeypatch.setitem(
        registry._ADAPTER_FACTORIES,
        "lever",
        lambda: LeverSourceAdapter(http_get=fake_http_get),
    )

    with ControlledBackgroundExecutor(clock=IncrementingClock()) as executor:
        submission = executor.submit_company(company=company_record)
        result = submission.submitted[0].future.result(timeout=10)

    result.scrape_run.refresh_from_db()
    assert submission.submitted_source_ids == (company_record.sources.get().pk,)
    assert result.scrape_run.status == "success"
    assert result.scrape_run.company_source_id == company_record.sources.get().pk
    assert result.scrape_run.requests_made == 1
    assert len(requested_urls) == 1
    assert requested_urls[0].startswith("https://api.lever.co/v0/postings/olo?")


def test_lever_multi_page_submission_persists_complete_snapshot_without_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = model("companies.Company").objects.create(
        name="Olo",
        source="lever",
        source_jobs_url="https://jobs.lever.co/olo",
    )
    add_company_source(company_record)
    responses = iter(
        [
            (LEVER_FIXTURES / "postings_page_1.json").read_text(encoding="utf-8"),
            (LEVER_FIXTURES / "postings_page_2.json").read_text(encoding="utf-8"),
        ]
    )
    requested_urls: list[str] = []
    selected_adapters: list[LeverSourceAdapter] = []

    def fake_http_get(url: str, timeout_seconds: float) -> str:
        requested_urls.append(url)
        assert timeout_seconds > 0
        return next(responses)

    def lever_factory() -> LeverSourceAdapter:
        adapter = LeverSourceAdapter(http_get=fake_http_get, page_size=2)
        selected_adapters.append(adapter)
        return adapter

    registry = importlib.import_module("scraping.sources.registry")
    monkeypatch.setitem(registry._ADAPTER_FACTORIES, "lever", lever_factory)

    with ControlledBackgroundExecutor(clock=IncrementingClock()) as executor:
        result = executor.submit_pipeline(company=company_record).future.result(timeout=10)

    stored_jobs = list(
        model("jobs.JobPosting").objects.filter(company=company_record).order_by("source_job_id")
    )
    result.scrape_run.refresh_from_db()

    assert len(selected_adapters) == 2
    assert all(isinstance(adapter, LeverSourceAdapter) for adapter in selected_adapters)
    assert len(requested_urls) == 2
    assert [parse_qs(urlsplit(url).query) for url in requested_urls] == [
        {"mode": ["json"], "limit": ["2"], "skip": ["0"]},
        {"mode": ["json"], "limit": ["2"], "skip": ["2"]},
    ]
    assert [job.source_job_id for job in stored_jobs] == [
        "lever-1",
        "lever-2",
        "lever-3",
    ]
    assert [job.title for job in stored_jobs] == [
        "Senior Data Engineer",
        "Support Specialist",
        "Office Manager",
    ]
    assert [job.workplace_type for job in stored_jobs] == ["hybrid", "remote", "onsite"]
    assert all(job.source == "lever" and job.status == "active" for job in stored_jobs)
    assert all(job.company_source_id == company_record.sources.get().pk for job in stored_jobs)
    assert result.jobs_found == 3
    assert result.jobs_created == 3
    assert result.scrape_run.status == "success"
    assert result.scrape_run.requests_made == 2
    assert result.reconciliation is not None
    assert result.reconciliation.total_company_jobs == 3
    assert result.reconciliation.seen_jobs == 3
    assert result.reconciliation.unseen_jobs == 0


def test_darwinbox_pipeline_is_complete_source_scoped_and_offline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = model("companies.Company").objects.create(
        name="Multi-source Company",
        source="darwinbox",
        source_jobs_url="https://tenant.darwinbox.com/ms/candidate/careers",
    )
    darwinbox_source = add_company_source(company_record)
    other_source = add_company_source(
        company_record,
        source="other-source",
        source_jobs_url="https://jobs.example.test/other",
    )
    first_responses = iter(
        [
            (DARWINBOX_FIXTURES / "page_1.json").read_text(encoding="utf-8"),
            (DARWINBOX_FIXTURES / "page_2_terminal.json").read_text(encoding="utf-8"),
        ]
    )
    darwinbox_calls: list[tuple[str, dict[str, object] | None]] = []

    def darwinbox_request(
        method: DarwinboxMethod,
        url: str,
        json_body: Mapping[str, object] | None,
        timeout_seconds: float,
    ) -> str:
        del url
        assert timeout_seconds > 0
        darwinbox_calls.append(
            (method, dict(json_body) if json_body is not None else None)
        )
        return next(first_responses)

    class OtherAdapter:
        def fetch(self, *, company: Any) -> SourceBatch:
            return SourceBatch(
                records=(
                    {
                        "source": company.source,
                        "source_job_id": "darwinbox-1",
                        "title": "Independent same ID",
                    },
                ),
                requests_made=0,
            )

    registry = importlib.import_module("scraping.sources.registry")
    monkeypatch.setitem(
        registry._ADAPTER_FACTORIES,
        "darwinbox",
        lambda: DarwinboxSourceAdapter(request=darwinbox_request),
    )
    monkeypatch.setitem(registry._ADAPTER_FACTORIES, "other-source", OtherAdapter)

    with ControlledBackgroundExecutor(clock=IncrementingClock()) as executor:
        darwinbox_result = executor.submit_source(
            company_source=darwinbox_source, miss_threshold=1
        ).future.result(timeout=10)
        other_result = executor.submit_source(
            company_source=other_source, miss_threshold=1
        ).future.result(timeout=10)

    darwinbox_jobs = model("jobs.JobPosting").objects.filter(
        company_source=darwinbox_source
    )
    assert [call[0] for call in darwinbox_calls] == ["POST", "POST"]
    assert [cast(dict[str, object], call[1])["page"] for call in darwinbox_calls] == [1, 2]
    stored_darwinbox_ids = list(
        darwinbox_jobs.order_by("source_job_id").values_list(
            "source_job_id", flat=True
        )
    )
    assert stored_darwinbox_ids == [
        "darwinbox-1",
        "darwinbox-2",
        "darwinbox-3",
    ]
    assert darwinbox_result.jobs_found == 3
    assert darwinbox_result.scrape_run.status == "success"
    assert darwinbox_result.scrape_run.requests_made == 2
    assert darwinbox_result.scrape_run.company_source_id == darwinbox_source.pk
    assert darwinbox_jobs.filter(source="darwinbox").count() == 3
    assert other_result.scrape_run.status == "success"
    same_id_jobs = model("jobs.JobPosting").objects.filter(source_job_id="darwinbox-1")
    assert same_id_jobs.count() == 2
    assert set(same_id_jobs.values_list("company_source_id", flat=True)) == {
        darwinbox_source.pk,
        other_source.pk,
    }

    terminal_response = json.dumps(
        {
            "status": "success",
            "data": [
                {
                    "id": "darwinbox-2",
                    "title": "Delivery Manager",
                    "jd": "Still active.",
                }
            ],
            "job_counts": 1,
        }
    )
    monkeypatch.setitem(
        registry._ADAPTER_FACTORIES,
        "darwinbox",
        lambda: DarwinboxSourceAdapter(
            request=lambda method, url, body, timeout: terminal_response
        ),
    )
    with ControlledBackgroundExecutor(clock=IncrementingClock()) as executor:
        second_result = executor.submit_source(
            company_source=darwinbox_source, miss_threshold=1
        ).future.result(timeout=10)

    assert second_result.reconciliation is not None
    assert second_result.reconciliation.jobs_marked_not_found == 2
    assert model("jobs.JobPosting").objects.get(
        company_source=darwinbox_source, source_job_id="darwinbox-1"
    ).status == "not_found"
    assert model("jobs.JobPosting").objects.get(
        company_source=other_source, source_job_id="darwinbox-1"
    ).status == "active"


def test_incomplete_darwinbox_pagination_fails_without_reconciliation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = model("companies.Company").objects.create(
        name="Incomplete Darwinbox",
        source="darwinbox",
        source_jobs_url="https://tenant.darwinbox.com/ms/candidate/careers",
    )
    company_source = add_company_source(company_record)
    registry = importlib.import_module("scraping.sources.registry")
    complete = json.dumps(
        {
            "status": "success",
            "data": [{"id": "existing", "title": "Existing", "jd": "Retain."}],
            "job_counts": 1,
        }
    )
    monkeypatch.setitem(
        registry._ADAPTER_FACTORIES,
        "darwinbox",
        lambda: DarwinboxSourceAdapter(
            request=lambda method, url, body, timeout: complete
        ),
    )
    with ControlledBackgroundExecutor(clock=IncrementingClock()) as executor:
        executor.submit_source(company_source=company_source).future.result(timeout=10)

    existing = model("jobs.JobPosting").objects.get(company_source=company_source)
    incomplete = json.dumps(
        {
            "status": "success",
            "data": [{"id": "partial", "title": "Partial", "jd": "Partial."}],
            "job_counts": 100,
        }
    )
    monkeypatch.setitem(
        registry._ADAPTER_FACTORIES,
        "darwinbox",
        lambda: DarwinboxSourceAdapter(
            request=lambda method, url, body, timeout: incomplete,
            max_pages=1,
        ),
    )

    with (
        ControlledBackgroundExecutor(clock=IncrementingClock()) as executor,
        pytest.raises(FixturePipelineError) as caught,
    ):
        executor.submit_source(
            company_source=company_source, miss_threshold=1
        ).future.result(timeout=10)

    caught.value.scrape_run.refresh_from_db()
    existing.refresh_from_db()
    assert caught.value.scrape_run.status == "failed"
    assert caught.value.scrape_run.requests_made == 1
    assert existing.status == "active"
    assert existing.consecutive_successful_misses == 0
    assert model("jobs.JobPosting").objects.filter(
        company_source=company_source, source_job_id="partial"
    ).exists() is False


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
    assert result.scrape_run.finished_at == datetime(2026, 8, 8, 9, 0, 4, tzinfo=UTC)


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
        worker_source = cast(Any, kwargs["company_source"])
        calls.append(worker_source.company_id)
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
            thread.name.startswith("source-pipeline") for thread in threading.enumerate()
        )
    finally:
        release.set()
        shutdown_thread.join(timeout=5)
        executor.shutdown(wait=True)


def test_two_sources_of_same_company_have_independent_active_task_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = company()
    first_source = company_record.sources.get()
    second_source = add_company_source(
        company_record,
        source_jobs_url="https://jobs.example.test/background/second",
    )
    entered = {first_source.pk: threading.Event(), second_source.pk: threading.Event()}
    release = threading.Event()
    sentinel = cast(FixturePipelineResult, object())

    def blocked_pipeline(**kwargs: object) -> FixturePipelineResult:
        source = cast(Any, kwargs["company_source"])
        entered[source.pk].set()
        if not release.wait(timeout=5):
            raise TimeoutError("test did not release source workers")
        return sentinel

    monkeypatch.setattr("scraping.background.run_source_pipeline", blocked_pipeline)
    executor = ControlledBackgroundExecutor(max_workers=2)
    try:
        first = executor.submit_source(company_source=first_source)
        second = executor.submit_source(company_source=second_source)
        assert entered[first_source.pk].wait(timeout=5)
        assert entered[second_source.pk].wait(timeout=5)
        assert first.company_source_id == first_source.pk
        assert second.company_source_id == second_source.pk
        with pytest.raises(BackgroundRunAlreadyScheduledError):
            executor.submit_source(company_source=first_source)
        release.set()
        assert first.future.result(timeout=5) is sentinel
        assert second.future.result(timeout=5) is sentinel
    finally:
        release.set()
        executor.shutdown()


@pytest.mark.parametrize(
    "approval_status",
    ["needs_review", "blocked", "rejected"],
)
def test_company_submission_skips_nonapproved_sources(
    approval_status: str,
) -> None:
    company_record = company()
    internal_source = add_company_source(
        company_record,
        source="review-source",
        source_jobs_url=f"https://jobs.example.test/{approval_status}",
        approval_status=approval_status,
        is_active=False,
    )

    with ControlledBackgroundExecutor() as executor:
        result = executor.submit_company(company=company_record)
        for handle in result.submitted:
            handle.future.result(timeout=10)

    assert result.submitted_source_ids == (company_record.sources.get(source="fixture").pk,)
    assert result.skipped_source_ids == (internal_source.pk,)


def test_company_submission_skips_inactive_approved_source() -> None:
    company_record = company()
    inactive_source = add_company_source(
        company_record,
        source_jobs_url="https://jobs.example.test/background/inactive",
        is_active=False,
    )

    with ControlledBackgroundExecutor() as executor:
        result = executor.submit_company(company=company_record)
        for handle in result.submitted:
            handle.future.result(timeout=10)

    assert result.skipped_source_ids == (inactive_source.pk,)


def test_company_submission_skips_registered_but_unavailable_darwinbox() -> None:
    company_record = company()
    darwinbox_source = add_company_source(
        company_record,
        source="darwinbox",
        source_jobs_url="https://tenant.darwinbox.com/ms/candidate/careers",
    )

    with ControlledBackgroundExecutor() as executor:
        result = executor.submit_company(company=company_record)
        for handle in result.submitted:
            handle.future.result(timeout=10)

    assert result.submitted_source_ids == (company_record.sources.get(source="fixture").pk,)
    assert result.skipped_source_ids == (darwinbox_source.pk,)
    assert not model("scrape_runs.ScrapeRun").objects.filter(
        company_source=darwinbox_source
    ).exists()


def test_inactive_company_and_zero_executable_sources_fail_closed() -> None:
    inactive_company = company(name="Inactive Company")
    inactive_company.is_active = False
    inactive_company.save(update_fields=("is_active", "updated_at"))
    no_sources_company = company(name="No Sources")
    no_sources_company.sources.update(is_active=False)

    with ControlledBackgroundExecutor() as executor:
        with pytest.raises(BackgroundInactiveCompanyError):
            executor.submit_company(company=inactive_company)
        with pytest.raises(BackgroundNoExecutableSourcesError):
            executor.submit_company(company=no_sources_company)

    assert model("scrape_runs.ScrapeRun").objects.count() == 0


def test_unknown_registered_key_failure_is_reported_without_submission() -> None:
    company_record = company()
    company_record.sources.update(is_active=False)
    unknown_source = add_company_source(
        company_record,
        source="unknown",
        source_jobs_url="https://jobs.example.test/unknown",
    )

    with ControlledBackgroundExecutor() as executor:
        result = executor.submit_company(company=company_record)

    assert result.submitted_source_ids == ()
    assert result.failed_source_ids == (unknown_source.pk,)
    assert model("scrape_runs.ScrapeRun").objects.count() == 0


def test_already_running_source_does_not_block_second_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = company()
    first_source = company_record.sources.get()
    second_source = add_company_source(
        company_record,
        source_jobs_url="https://jobs.example.test/background/eligible",
    )
    model("scrape_runs.ScrapeRun").objects.create(
        company=company_record,
        company_source=first_source,
    )
    sentinel = cast(FixturePipelineResult, object())
    monkeypatch.setattr(
        "scraping.background.run_source_pipeline",
        lambda **kwargs: sentinel,
    )

    with ControlledBackgroundExecutor() as executor:
        result = executor.submit_company(company=company_record)
        assert result.submitted[0].future.result(timeout=5) is sentinel

    assert result.already_running_source_ids == (first_source.pk,)
    assert result.submitted_source_ids == (second_source.pk,)


def test_company_submission_runs_two_sources_in_deterministic_order() -> None:
    company_record = company()
    first_source = company_record.sources.get()
    second_source = add_company_source(
        company_record,
        source_jobs_url="https://jobs.example.test/background/second",
    )

    with ControlledBackgroundExecutor(clock=IncrementingClock()) as executor:
        result = executor.submit_company(company=company_record)
        outcomes = [handle.future.result(timeout=10) for handle in result.submitted]

    assert result.submitted_source_ids == (first_source.pk, second_source.pk)
    assert [outcome.scrape_run.company_source_id for outcome in outcomes] == [
        first_source.pk,
        second_source.pk,
    ]
    assert model("scrape_runs.ScrapeRun").objects.filter(status="success").count() == 2
    assert model("jobs.JobPosting").objects.filter(company=company_record).count() == 6


def test_source_failure_does_not_rollback_other_source_success(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    company_record = company()
    company_record.sources.all().delete()
    success_source = add_company_source(
        company_record,
        source="success-source",
        source_jobs_url="https://jobs.example.test/success",
    )
    failed_source = add_company_source(
        company_record,
        source="failed-source",
        source_jobs_url="https://jobs.example.test/failed",
    )

    class SuccessAdapter:
        def fetch(self, *, company: Any) -> SourceBatch:
            return SourceBatch(
                records=(
                    {
                        "source": company.source,
                        "source_job_id": "shared-1",
                        "title": "Retained success",
                    },
                ),
                requests_made=1,
            )

    class FailedAdapter:
        def fetch(self, *, company: Any) -> SourceBatch:
            del company
            raise SourceError("synthetic source failure", requests_made=2)

    registry = importlib.import_module("scraping.sources.registry")
    monkeypatch.setitem(registry._ADAPTER_FACTORIES, "success-source", SuccessAdapter)
    monkeypatch.setitem(registry._ADAPTER_FACTORIES, "failed-source", FailedAdapter)

    with ControlledBackgroundExecutor(clock=IncrementingClock()) as executor:
        result = executor.submit_company(company=company_record)
        success = result.submitted[0].future.result(timeout=10)
        with pytest.raises(FixturePipelineError):
            result.submitted[1].future.result(timeout=10)

    runs = {
        run.company_source_id: run
        for run in model("scrape_runs.ScrapeRun").objects.filter(company=company_record)
    }
    assert success.scrape_run.company_source_id == success_source.pk
    assert runs[success_source.pk].status == "success"
    assert runs[success_source.pk].requests_made == 1
    assert runs[failed_source.pk].status == "failed"
    assert runs[failed_source.pk].requests_made == 2
    assert model("jobs.JobPosting").objects.get().company_source_id == success_source.pk
    company_record.refresh_from_db()
    assert company_record.last_scrape_status == "failed"
