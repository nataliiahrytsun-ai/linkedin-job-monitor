"""Controlled source-level background execution and Company orchestration."""

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
from scraping.sources.registry import (
    executable_source_keys,
    get_source_adapter,
    normalize_source_key,
    registered_source_keys,
)
from scraping.sources.resolution import resolve_legacy_company_source


@dataclass(frozen=True, slots=True)
class BackgroundRunHandle:
    """Identity and Future for one accepted CompanySource task."""

    task_id: str
    company_id: int
    company_source_id: int
    future: Future[PipelineResult]


@dataclass(frozen=True, slots=True)
class CompanySubmissionResult:
    """Deterministic outcome of independently submitting one Company's sources."""

    company_id: int
    submitted: tuple[BackgroundRunHandle, ...]
    already_running_source_ids: tuple[int, ...]
    skipped_source_ids: tuple[int, ...]
    failed_source_ids: tuple[int, ...]

    @property
    def submitted_source_ids(self) -> tuple[int, ...]:
        return tuple(handle.company_source_id for handle in self.submitted)


class BackgroundExecutionError(Exception):
    """Base error for rejected controlled-background operations."""


class InvalidMaxWorkersError(BackgroundExecutionError, ValueError):
    """The executor worker limit is not a positive integer."""


class BackgroundCompanyNotSavedError(BackgroundExecutionError):
    """The submitted Company or CompanySource is not persisted."""


class BackgroundInactiveCompanyError(BackgroundExecutionError):
    """An inactive Company cannot submit source work."""


class BackgroundNoExecutableSourcesError(BackgroundExecutionError):
    """A Company has no approved active source to submit."""


class BackgroundRunAlreadyScheduledError(BackgroundExecutionError):
    """This CompanySource already has queued or running work."""


class BackgroundExecutorShutdownError(BackgroundExecutionError):
    """The executor has been shut down and accepts no new work."""


class InvalidShutdownWaitError(BackgroundExecutionError, ValueError):
    """The shutdown wait option is not a bool."""


class InvalidBackgroundClockError(BackgroundExecutionError, ValueError):
    """The injected execution clock is not callable."""


class BackgroundSourceError(BackgroundExecutionError):
    """A CompanySource cannot be submitted through the adapter registry."""


class ControlledBackgroundExecutor:
    """A bounded thread pool keyed by CompanySource execution ownership."""

    def __init__(
        self,
        *,
        max_workers: int = 2,
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
            thread_name_prefix="source-pipeline",
        )
        self.max_workers = max_workers
        self._clock = clock
        self._lock = Lock()
        self._active: dict[int, Future[PipelineResult] | None] = {}
        self._shutdown = False

    @staticmethod
    def _validated_company(company: CompanyRecord) -> Any:
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
        stored_company = model.objects.get(pk=company_pk)
        if not stored_company.is_active:
            raise BackgroundInactiveCompanyError(
                "inactive company cannot submit background work"
            )
        return stored_company

    @staticmethod
    def _validated_source(company_source: CompanySourceRecord) -> tuple[Any, Any]:
        source_model = apps.get_model("companies", "CompanySource")
        source_pk = getattr(company_source, "pk", None)
        if (
            not isinstance(company_source, source_model)
            or type(source_pk) is not int
            or getattr(getattr(company_source, "_state", None), "adding", True)
        ):
            raise BackgroundCompanyNotSavedError(
                "company source must already be saved before background submission"
            )
        try:
            stored_source = source_model.objects.select_related("company").get(pk=source_pk)
        except source_model.DoesNotExist as error:
            raise BackgroundCompanyNotSavedError(
                "company source must already be saved before background submission"
            ) from error
        if company_source.company_id != stored_source.company_id:
            raise BackgroundCompanyNotSavedError(
                "company source company does not match persisted ownership"
            )
        if not stored_source.company.is_active:
            raise BackgroundInactiveCompanyError(
                "inactive company cannot submit background work"
            )
        if not stored_source.is_active or stored_source.approval_status != "approved":
            raise BackgroundSourceError("company source must be approved and active")
        try:
            adapter = get_source_adapter(stored_source)
        except SourceError as error:
            raise BackgroundSourceError(str(error)) from error
        return stored_source, adapter

    def _run_source_pipeline(
        self,
        *,
        company_source_id: int,
        recover_job_errors: bool,
        miss_threshold: int,
    ) -> PipelineResult:
        close_old_connections()
        try:
            worker_source = self._worker_source(company_source_id)
            stored_source, adapter = self._validated_source(worker_source)
            return run_source_pipeline(
                company_source=cast(CompanySourceRecord, stored_source),
                adapter=adapter,
                miss_threshold=miss_threshold,
                recover_job_errors=recover_job_errors,
                clock=self._clock,
            )
        finally:
            close_old_connections()

    @staticmethod
    def _worker_source(company_source_id: int) -> Any:
        """Load queued work afresh and fail cleanly if its owner was deleted."""
        source_model = apps.get_model("companies", "CompanySource")
        try:
            return source_model.objects.select_related("company").get(
                pk=company_source_id
            )
        except source_model.DoesNotExist as error:
            raise BackgroundCompanyNotSavedError(
                "company source was deleted before background work started"
            ) from error

    def _run_fixture_pipeline(
        self,
        *,
        company_source_id: int,
        fixture_path: Path,
        recover_job_errors: bool,
        miss_threshold: int,
    ) -> FixturePipelineResult:
        close_old_connections()
        try:
            worker_source = self._worker_source(company_source_id)
            stored_source, _adapter = self._validated_source(worker_source)
            if normalize_source_key(stored_source.source) != "fixture":
                raise BackgroundSourceError(
                    "explicit fixture execution requires a fixture CompanySource"
                )
            return run_fixture_pipeline(
                company_source=cast(CompanySourceRecord, stored_source),
                fixture_path=fixture_path,
                miss_threshold=miss_threshold,
                recover_job_errors=recover_job_errors,
                clock=self._clock,
            )
        finally:
            close_old_connections()

    def _release_source(
        self, company_source_id: int, future: Future[PipelineResult]
    ) -> None:
        with self._lock:
            if self._active.get(company_source_id) is future:
                self._active.pop(company_source_id, None)

    def _queue_source(
        self,
        *,
        company_source: Any,
        worker: Callable[..., PipelineResult],
        worker_kwargs: dict[str, object],
    ) -> BackgroundRunHandle:
        source_id = cast(int, company_source.pk)
        run_model = apps.get_model("scrape_runs", "ScrapeRun")
        if run_model.objects.filter(
            company_source_id=source_id,
            status="running",
        ).exists():
            raise BackgroundRunAlreadyScheduledError(
                "company source already has a RUNNING scrape run"
            )
        with self._lock:
            if self._shutdown:
                raise BackgroundExecutorShutdownError(
                    "background executor has been shut down"
                )
            existing = self._active.get(source_id)
            if source_id in self._active:
                if existing is not None and existing.done():
                    self._active.pop(source_id, None)
                else:
                    raise BackgroundRunAlreadyScheduledError(
                        "company source already has queued or running work"
                    )
            self._active[source_id] = None
        try:
            future = self._executor.submit(worker, **worker_kwargs)
        except Exception:
            with self._lock:
                if self._active.get(source_id) is None:
                    self._active.pop(source_id, None)
            raise
        with self._lock:
            self._active[source_id] = future
        future.add_done_callback(partial(self._release_source, source_id))
        return BackgroundRunHandle(
            task_id=uuid4().hex,
            company_id=company_source.company_id,
            company_source_id=source_id,
            future=future,
        )

    def submit_source(
        self,
        *,
        company_source: CompanySourceRecord,
        recover_job_errors: bool = True,
        miss_threshold: int = DEFAULT_SUCCESSFUL_MISS_THRESHOLD,
    ) -> BackgroundRunHandle:
        """Queue one explicit CompanySource through its registered adapter."""
        stored_source, _adapter = self._validated_source(company_source)
        return self._queue_source(
            company_source=stored_source,
            worker=self._run_source_pipeline,
            worker_kwargs={
                "company_source_id": stored_source.pk,
                "recover_job_errors": recover_job_errors,
                "miss_threshold": miss_threshold,
            },
        )

    def submit_company(
        self,
        *,
        company: CompanyRecord,
        recover_job_errors: bool = True,
        miss_threshold: int = DEFAULT_SUCCESSFUL_MISS_THRESHOLD,
    ) -> CompanySubmissionResult:
        """Independently submit every approved active source in PK order."""
        stored_company = self._validated_company(company)
        sources = tuple(stored_company.sources.select_related("company").order_by("pk"))
        registered_keys = set(registered_source_keys())
        executable_keys = set(executable_source_keys())
        blocked_keys = registered_keys - executable_keys
        skipped = tuple(
            source.pk
            for source in sources
            if not source.is_active
            or source.approval_status != "approved"
            or normalize_source_key(source.source) in blocked_keys
        )
        candidates = tuple(
            source
            for source in sources
            if source.is_active
            and source.approval_status == "approved"
            and normalize_source_key(source.source) not in blocked_keys
        )
        if not candidates:
            raise BackgroundNoExecutableSourcesError(
                "company has no approved active source to execute"
            )

        submitted: list[BackgroundRunHandle] = []
        already_running: list[int] = []
        failed: list[int] = []
        for source in candidates:
            try:
                handle = self.submit_source(
                    company_source=source,
                    recover_job_errors=recover_job_errors,
                    miss_threshold=miss_threshold,
                )
            except BackgroundRunAlreadyScheduledError:
                already_running.append(source.pk)
            except BackgroundExecutionError:
                failed.append(source.pk)
            else:
                submitted.append(handle)
        return CompanySubmissionResult(
            company_id=stored_company.pk,
            submitted=tuple(submitted),
            already_running_source_ids=tuple(already_running),
            skipped_source_ids=skipped,
            failed_source_ids=tuple(failed),
        )

    def submit_pipeline(
        self,
        *,
        company: CompanyRecord,
        recover_job_errors: bool = True,
        miss_threshold: int = DEFAULT_SUCCESSFUL_MISS_THRESHOLD,
    ) -> BackgroundRunHandle:
        """Backward-compatible single-source submission for legacy callers."""
        self._validated_company(company)
        try:
            company_source = cast(
                CompanySourceRecord,
                resolve_legacy_company_source(company),
            )
        except SourceError as error:
            raise BackgroundSourceError(str(error)) from error
        return self.submit_source(
            company_source=company_source,
            recover_job_errors=recover_job_errors,
            miss_threshold=miss_threshold,
        )

    def submit_fixture_source(
        self,
        *,
        company_source: CompanySourceRecord,
        fixture_path: Path,
        recover_job_errors: bool = True,
        miss_threshold: int = DEFAULT_SUCCESSFUL_MISS_THRESHOLD,
    ) -> BackgroundRunHandle:
        """Queue an explicit internal fixture CompanySource."""
        stored_source, _adapter = self._validated_source(company_source)
        if normalize_source_key(stored_source.source) != "fixture":
            raise BackgroundSourceError(
                "explicit fixture execution requires a fixture CompanySource"
            )
        return self._queue_source(
            company_source=stored_source,
            worker=self._run_fixture_pipeline,
            worker_kwargs={
                "company_source_id": stored_source.pk,
                "fixture_path": fixture_path,
                "recover_job_errors": recover_job_errors,
                "miss_threshold": miss_threshold,
            },
        )

    def submit_fixture_pipeline(
        self,
        *,
        company: CompanyRecord,
        fixture_path: Path,
        recover_job_errors: bool = True,
        miss_threshold: int = DEFAULT_SUCCESSFUL_MISS_THRESHOLD,
    ) -> BackgroundRunHandle:
        """Backward-compatible internal fixture submission."""
        self._validated_company(company)
        try:
            company_source = cast(
                CompanySourceRecord,
                resolve_legacy_company_source(company),
            )
        except SourceError as error:
            raise BackgroundSourceError(str(error)) from error
        return self.submit_fixture_source(
            company_source=company_source,
            fixture_path=fixture_path,
            recover_job_errors=recover_job_errors,
            miss_threshold=miss_threshold,
        )

    def shutdown(self, wait: bool = True) -> None:
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
