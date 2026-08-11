"""Controlled in-process background execution for source-neutral pipelines."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from functools import partial
from pathlib import Path
from threading import Lock
from typing import Any, cast
from uuid import uuid4

from django.apps import apps  # type: ignore[import-untyped]
from django.db import close_old_connections  # type: ignore[import-untyped]
from django.utils import timezone  # type: ignore[import-untyped]

from scraping.pipeline import (
    CompanyRecord,
    CompanySourceRecord,
    FixturePipelineResult,
    PipelineResult,
    run_fixture_pipeline,
    run_source_pipeline,
)
from scraping.reconciliation import DEFAULT_SUCCESSFUL_MISS_THRESHOLD
from scraping.sources.base import SourceError
from scraping.sources.registry import get_source_adapter, normalize_source_key
from scraping.sources.resolution import resolve_legacy_company_source


@dataclass(frozen=True, slots=True)
class BackgroundRunHandle:
    """Identity and Future for one accepted in-process fixture task."""

    task_id: str
    company_id: int
    future: Future[FixturePipelineResult]


class BackgroundExecutionError(Exception):
    """Base error for rejected controlled-background operations."""


class InvalidMaxWorkersError(BackgroundExecutionError, ValueError):
    """The executor worker limit is not a positive integer."""


class BackgroundCompanyNotSavedError(BackgroundExecutionError):
    """The submitted company is not a persisted Company row."""


class BackgroundRunAlreadyScheduledError(BackgroundExecutionError):
    """This executor already has a queued or running task for the company."""


class BackgroundExecutorShutdownError(BackgroundExecutionError):
    """The executor has been shut down and accepts no new work."""


class InvalidShutdownWaitError(BackgroundExecutionError, ValueError):
    """The shutdown wait option is not a bool."""


class InvalidBackgroundClockError(BackgroundExecutionError, ValueError):
    """The injected execution clock is not callable."""


class BackgroundSourceError(BackgroundExecutionError):
    """A company source cannot be submitted through the adapter registry."""


class ControlledBackgroundExecutor:
    """A bounded, explicitly managed thread pool for company update tasks.

    The active-company registry protects only this executor instance in this
    Python process. The database constraint on RUNNING ScrapeRun rows remains
    the final duplicate-run protection; multi-process coordination is outside
    this prototype executor.
    """

    def __init__(
        self,
        *,
        max_workers: int = 1,
        clock: Callable[[], datetime] = timezone.now,
    ) -> None:
        if type(max_workers) is not int or max_workers < 1:
            raise InvalidMaxWorkersError(
                "max_workers must be an int greater than or equal to 1, excluding bool"
            )
        if not callable(clock):
            raise InvalidBackgroundClockError("clock must be callable")

        self._executor = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix="fixture-pipeline",
        )
        self._clock = clock
        self._lock = Lock()
        self._active: dict[int, Future[FixturePipelineResult] | None] = {}
        self._shutdown = False

    @staticmethod
    def _validated_company_id(company: CompanyRecord) -> int:
        model = apps.get_model("companies", "Company")
        company_pk = getattr(company, "pk", None)
        if (
            not isinstance(company, model)
            or type(company_pk) is not int
            or getattr(getattr(company, "_state", None), "adding", True)
            or not model.objects.filter(pk=company_pk).exists()
        ):
            raise BackgroundCompanyNotSavedError(
                "company must already be saved before background submission"
            )
        return company_pk

    def _run_fixture_pipeline(
        self,
        *,
        company_id: int,
        fixture_path: Path,
        recover_job_errors: bool,
        miss_threshold: int,
    ) -> FixturePipelineResult:
        close_old_connections()
        try:
            company_model = apps.get_model("companies", "Company")
            worker_company = company_model.objects.get(pk=company_id)
            try:
                company_source = cast(
                    CompanySourceRecord,
                    resolve_legacy_company_source(worker_company),
                )
            except SourceError as error:
                raise BackgroundSourceError(str(error)) from error
            return run_fixture_pipeline(
                company_source=company_source,
                fixture_path=fixture_path,
                miss_threshold=miss_threshold,
                recover_job_errors=recover_job_errors,
                clock=self._clock,
            )
        finally:
            close_old_connections()

    def _run_source_pipeline(
        self,
        *,
        company_id: int,
        recover_job_errors: bool,
        miss_threshold: int,
    ) -> PipelineResult:
        close_old_connections()
        try:
            company_model = apps.get_model("companies", "Company")
            worker_company = company_model.objects.get(pk=company_id)
            try:
                company_source = cast(
                    CompanySourceRecord,
                    resolve_legacy_company_source(worker_company),
                )
                adapter = get_source_adapter(company_source)
            except SourceError as error:
                raise BackgroundSourceError(str(error)) from error
            return run_source_pipeline(
                company_source=company_source,
                adapter=adapter,
                miss_threshold=miss_threshold,
                recover_job_errors=recover_job_errors,
                clock=self._clock,
            )
        finally:
            close_old_connections()

    def _release_company(
        self, company_id: int, future: Future[FixturePipelineResult]
    ) -> None:
        with self._lock:
            if self._active.get(company_id) is future:
                self._active.pop(company_id, None)

    def submit_fixture_pipeline(
        self,
        *,
        company: CompanyRecord,
        fixture_path: Path,
        recover_job_errors: bool = True,
        miss_threshold: int = DEFAULT_SUCCESSFUL_MISS_THRESHOLD,
    ) -> BackgroundRunHandle:
        """Queue one company fixture run and return without waiting for it."""
        company_id = self._validated_company_id(company)
        try:
            company_source = resolve_legacy_company_source(company)
        except SourceError as error:
            raise BackgroundSourceError(str(error)) from error
        if normalize_source_key(company_source.source) != "fixture":
            raise BackgroundSourceError(
                "explicit fixture execution requires a fixture CompanySource"
            )
        task_id = uuid4().hex

        with self._lock:
            if self._shutdown:
                raise BackgroundExecutorShutdownError(
                    "background executor has been shut down"
                )
            existing = self._active.get(company_id)
            if company_id in self._active:
                if existing is not None and existing.done():
                    self._active.pop(company_id, None)
                else:
                    raise BackgroundRunAlreadyScheduledError(
                        "company already has a queued or running background task"
                    )
            self._active[company_id] = None

        try:
            future = self._executor.submit(
                self._run_fixture_pipeline,
                company_id=company_id,
                fixture_path=fixture_path,
                recover_job_errors=recover_job_errors,
                miss_threshold=miss_threshold,
            )
        except Exception:
            with self._lock:
                if self._active.get(company_id) is None:
                    self._active.pop(company_id, None)
            raise

        with self._lock:
            self._active[company_id] = future
        future.add_done_callback(partial(self._release_company, company_id))
        return BackgroundRunHandle(
            task_id=task_id,
            company_id=company_id,
            future=future,
        )

    def submit_pipeline(
        self,
        *,
        company: CompanyRecord,
        recover_job_errors: bool = True,
        miss_threshold: int = DEFAULT_SUCCESSFUL_MISS_THRESHOLD,
    ) -> BackgroundRunHandle:
        """Queue a source-neutral company update and return without waiting."""
        company_id = self._validated_company_id(company)
        try:
            company_source = resolve_legacy_company_source(company)
            get_source_adapter(company_source)
        except SourceError as error:
            raise BackgroundSourceError(str(error)) from error
        task_id = uuid4().hex

        with self._lock:
            if self._shutdown:
                raise BackgroundExecutorShutdownError(
                    "background executor has been shut down"
                )
            existing = self._active.get(company_id)
            if company_id in self._active:
                if existing is not None and existing.done():
                    self._active.pop(company_id, None)
                else:
                    raise BackgroundRunAlreadyScheduledError(
                        "company already has a queued or running background task"
                    )
            self._active[company_id] = None

        try:
            future = self._executor.submit(
                self._run_source_pipeline,
                company_id=company_id,
                recover_job_errors=recover_job_errors,
                miss_threshold=miss_threshold,
            )
        except Exception:
            with self._lock:
                if self._active.get(company_id) is None:
                    self._active.pop(company_id, None)
            raise

        with self._lock:
            self._active[company_id] = future
        future.add_done_callback(partial(self._release_company, company_id))
        return BackgroundRunHandle(
            task_id=task_id,
            company_id=company_id,
            future=future,
        )

    def shutdown(self, wait: bool = True) -> None:
        """Reject new tasks and shut down the owned thread pool."""
        if type(wait) is not bool:
            raise InvalidShutdownWaitError("wait must be a bool")
        with self._lock:
            self._shutdown = True
        self._executor.shutdown(wait=wait)

    def __enter__(self) -> ControlledBackgroundExecutor:
        return self

    def __exit__(
        self,
        exception_type: type[BaseException] | None,
        exception: BaseException | None,
        traceback: Any,
    ) -> None:
        self.shutdown(wait=True)
