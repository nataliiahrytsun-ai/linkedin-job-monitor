from __future__ import annotations

import importlib
import os
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, cast

import pytest


@pytest.fixture(scope="module", autouse=True)
def migrated_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    apps = importlib.import_module("django.apps").apps
    if not apps.ready:
        database_path = tmp_path_factory.mktemp("company-deletion-db") / "deletion.sqlite3"
        os.environ["DJANGO_SETTINGS_MODULE"] = "job_monitor.settings"
        os.environ["JOB_MONITOR_SQLITE_PATH"] = str(database_path)
        importlib.import_module("django").setup()
    importlib.import_module("django.core.management").call_command(
        "migrate", interactive=False, verbosity=0
    )
    yield


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    importlib.import_module("django.core.management").call_command(
        "flush", interactive=False, verbosity=0
    )


def model(name: str) -> Any:
    return importlib.import_module("django.apps").apps.get_model(name)


def company(*, name: str, sequence: int) -> tuple[Any, Any]:
    company_record = model("companies.Company").objects.create(
        name=name,
        source="fixture",
        source_jobs_url=f"https://jobs.example.test/delete-{sequence}/openings",
    )
    source = model("companies.CompanySource").objects.create(
        company=company_record,
        source="fixture",
        source_jobs_url=company_record.source_jobs_url,
        approval_status="approved",
        is_active=True,
    )
    return company_record, source


def job(company_record: Any, source: Any, *, sequence: int) -> Any:
    return model("jobs.JobPosting").objects.create(
        company=company_record,
        company_source=source,
        source=source.source,
        source_job_id=f"delete-job-{sequence}",
        content_hash=f"{sequence:064x}",
        dedupe_key=f"{sequence + 100:064x}",
    )


def terminal_run(company_record: Any, source: Any, *, sequence: int) -> Any:
    started_at = datetime(2026, 8, 12, 10, sequence, tzinfo=UTC)
    return model("scrape_runs.ScrapeRun").objects.create(
        company=company_record,
        company_source=source,
        status="success",
        started_at=started_at,
        finished_at=started_at + timedelta(seconds=1),
        duration_seconds=Decimal("1.000"),
    )


def test_hard_delete_removes_owned_graph_and_preserves_unrelated_company() -> None:
    deletion = importlib.import_module("companies.deletion")
    target, first_source = company(name="Delete Target", sequence=1)
    second_source = model("companies.CompanySource").objects.create(
        company=target,
        source="fixture-two",
        source_jobs_url="https://jobs.example.test/delete-1/other",
        approval_status="approved",
        is_active=True,
    )
    job(target, first_source, sequence=1)
    job(target, second_source, sequence=2)
    terminal_run(target, first_source, sequence=1)
    terminal_run(target, second_source, sequence=2)

    unrelated, unrelated_source = company(name="Keep Target", sequence=2)
    unrelated_job = job(unrelated, unrelated_source, sequence=3)
    unrelated_run = terminal_run(unrelated, unrelated_source, sequence=3)
    target_id = target.pk
    source_ids = (first_source.pk, second_source.pk)

    result = deletion.delete_company(company_id=target_id)

    assert result.company_name == "Delete Target"
    assert result.sources_deleted == 2
    assert result.jobs_deleted == 2
    assert result.runs_deleted == 2
    assert not model("companies.Company").objects.filter(pk=target_id).exists()
    assert not model("companies.CompanySource").objects.filter(pk__in=source_ids).exists()
    assert not model("jobs.JobPosting").objects.filter(company_id=target_id).exists()
    assert not model("scrape_runs.ScrapeRun").objects.filter(company_id=target_id).exists()
    assert model("companies.Company").objects.filter(pk=unrelated.pk).exists()
    assert model("jobs.JobPosting").objects.filter(pk=unrelated_job.pk).exists()
    assert model("scrape_runs.ScrapeRun").objects.filter(pk=unrelated_run.pk).exists()


def test_running_run_blocks_hard_delete_without_partial_changes() -> None:
    deletion = importlib.import_module("companies.deletion")
    target, source = company(name="Running Target", sequence=1)
    posting = job(target, source, sequence=1)
    running = model("scrape_runs.ScrapeRun").objects.create(
        company=target,
        company_source=source,
        status="running",
    )

    with pytest.raises(deletion.CompanyDeletionBlockedError, match="still running"):
        deletion.delete_company(company_id=target.pk)

    assert model("companies.Company").objects.filter(pk=target.pk).exists()
    assert model("companies.CompanySource").objects.filter(pk=source.pk).exists()
    assert model("jobs.JobPosting").objects.filter(pk=posting.pk).exists()
    assert model("scrape_runs.ScrapeRun").objects.filter(pk=running.pk).exists()


def test_database_error_rolls_back_already_issued_deletes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deletion = importlib.import_module("companies.deletion")
    operational_error = importlib.import_module("django.db").OperationalError
    target, source = company(name="Rollback Target", sequence=1)
    posting = job(target, source, sequence=1)
    run = terminal_run(target, source, sequence=1)
    source_manager = model("companies.CompanySource").objects
    original_filter = source_manager.filter
    filter_calls = 0

    def fail_during_source_delete(*args: object, **kwargs: object) -> Any:
        nonlocal filter_calls
        filter_calls += 1
        if filter_calls == 2:
            raise operational_error("forced delete failure")
        return original_filter(*args, **kwargs)

    monkeypatch.setattr(source_manager, "filter", fail_during_source_delete)

    with pytest.raises(operational_error, match="forced delete failure"):
        deletion.delete_company(company_id=target.pk)

    assert model("companies.Company").objects.filter(pk=target.pk).exists()
    assert model("companies.CompanySource").objects.filter(pk=source.pk).exists()
    assert model("jobs.JobPosting").objects.filter(pk=posting.pk).exists()
    assert model("scrape_runs.ScrapeRun").objects.filter(pk=run.pk).exists()


def test_deleted_company_queued_task_fails_cleanly_and_releases_source_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    background = importlib.import_module("scraping.background")
    deletion = importlib.import_module("companies.deletion")
    blocker, blocker_source = company(name="Queue Blocker", sequence=1)
    target, target_source = company(name="Queued Delete Target", sequence=2)
    target_company_id = target.pk
    target_source_id = target_source.pk
    entered = threading.Event()
    release = threading.Event()
    target_done = threading.Event()
    sentinel = cast(Any, object())

    def controlled_pipeline(*, company_source: Any, **kwargs: object) -> Any:
        del kwargs
        if company_source.pk == blocker_source.pk:
            entered.set()
            if not release.wait(timeout=5):
                raise TimeoutError("test did not release the active worker")
        return sentinel

    monkeypatch.setattr("scraping.background.run_fixture_pipeline", controlled_pipeline)
    executor = background.ControlledBackgroundExecutor(max_workers=1)
    try:
        blocker_handle = executor.submit_fixture_source(
            company_source=blocker_source,
            fixture_path=Path("unused-blocker.json"),
        )
        assert entered.wait(timeout=5)
        target_handle = executor.submit_fixture_source(
            company_source=target_source,
            fixture_path=Path("unused-target.json"),
        )
        target_handle.future.add_done_callback(lambda _future: target_done.set())

        deletion.delete_company(company_id=target_company_id)
        assert not model("companies.Company").objects.filter(pk=target_company_id).exists()
        assert not model("companies.CompanySource").objects.filter(pk=target_source_id).exists()
        assert not model("jobs.JobPosting").objects.filter(
            company_id=target_company_id
        ).exists()
        assert not model("scrape_runs.ScrapeRun").objects.filter(
            company_id=target_company_id
        ).exists()

        release.set()
        assert blocker_handle.future.result(timeout=5) is sentinel
        with pytest.raises(
            background.BackgroundCompanyNotSavedError,
            match="deleted before background work started",
        ):
            target_handle.future.result(timeout=5)
        assert target_done.wait(timeout=5)

        recreated = model("companies.Company").objects.create(
            pk=target_company_id,
            name="Recreated Target",
            source="fixture",
            source_jobs_url="https://jobs.example.test/recreated/openings",
        )
        recreated_source = model("companies.CompanySource").objects.create(
            pk=target_source_id,
            company=recreated,
            source="fixture",
            source_jobs_url=recreated.source_jobs_url,
            approval_status="approved",
            is_active=True,
        )
        retry = executor.submit_fixture_source(
            company_source=recreated_source,
            fixture_path=Path("unused-retry.json"),
        )
        assert retry.future.result(timeout=5) is sentinel
    finally:
        release.set()
        executor.shutdown()
