"""Atomic persistence of normalized job postings."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol, cast

from django.apps import apps  # type: ignore[import-untyped]
from django.db import IntegrityError, transaction  # type: ignore[import-untyped]

from scraping.identity import compute_content_hash, compute_dedupe_key
from scraping.normalization import NormalizedJobPosting

_CONTENT_FIELDS = (
    "title",
    "country",
    "city",
    "location",
    "workplace_type",
    "employment_type",
    "compensation_text",
    "seniority_level",
    "job_function",
    "industry",
    "published_at",
    "description",
    "source_job_url",
)


class CompanyRecord(Protocol):
    pk: int | None


class CompanySourceRecord(Protocol):
    """The persisted source ownership required by this service."""

    pk: int | None
    company_id: int
    source: str

    @property
    def company(self) -> CompanyRecord: ...


class JobPostingRecord(Protocol):
    """The JobPosting attributes exposed in a persistence result."""

    pk: int
    company_id: int
    company: CompanyRecord
    company_source: CompanySourceRecord
    source: str
    source_job_id: str | None
    title: str | None
    country: str | None
    city: str | None
    location: str | None
    workplace_type: str | None
    employment_type: str | None
    compensation_text: str | None
    seniority_level: str | None
    job_function: str | None
    industry: str | None
    published_at: datetime | None
    description: str | None
    source_job_url: str | None
    content_hash: str
    last_reviewed_content_hash: str | None
    status: str
    first_seen_at: datetime
    last_seen_at: datetime
    dedupe_key: str
    consecutive_successful_misses: int

    def refresh_from_db(self) -> None: ...


class PersistenceOutcome(StrEnum):
    CREATED = "created"
    UPDATED = "updated"
    UNCHANGED = "unchanged"


@dataclass(frozen=True, slots=True)
class PersistenceResult:
    """Persisted row plus its content/identity outcome.

    UNCHANGED means business content and identity were unchanged. The service
    may still have refreshed last_seen_at and reset the successful-miss counter.
    """

    job_posting: JobPostingRecord
    outcome: PersistenceOutcome


class JobPersistenceError(Exception):
    """Base error for rejected or failed job persistence operations."""


class PersistenceValidationError(JobPersistenceError, ValueError):
    """The company, normalized job, or observation time is invalid."""


class IdentityConflictError(JobPersistenceError):
    """Separate rows claim the incoming source ID and fallback URL."""


class PersistenceConstraintError(JobPersistenceError):
    """A database constraint rejected the atomic persistence operation."""


def _job_posting_model() -> Any:
    return apps.get_model("jobs", "JobPosting")


def _company_source_model() -> Any:
    return apps.get_model("companies", "CompanySource")


def _validate_inputs(
    *,
    company_source: CompanySourceRecord,
    job: NormalizedJobPosting,
    seen_at: datetime,
) -> tuple[Any, str, str]:
    source_model = _company_source_model()
    source_pk = getattr(company_source, "pk", None)
    if (
        not isinstance(company_source, source_model)
        or source_pk is None
        or getattr(getattr(company_source, "_state", None), "adding", True)
        or not source_model.objects.filter(pk=source_pk).exists()
    ):
        raise PersistenceValidationError("company_source must already be saved")

    stored_source = source_model.objects.select_related("company").get(pk=source_pk)
    if company_source.company_id != stored_source.company_id:
        raise PersistenceValidationError(
            "company_source company does not match its persisted ownership"
        )

    normalized_company_source = stored_source.source.strip().lower()
    if not job.source:
        raise PersistenceValidationError("job.source must not be empty")
    if job.source != job.source.strip().lower():
        raise PersistenceValidationError("job.source must already be normalized")
    if job.source != normalized_company_source:
        raise PersistenceValidationError("job.source must match company_source.source")
    if job.source_job_id is not None and not job.source_job_id.strip():
        raise PersistenceValidationError("source_job_id must not be blank")
    if job.source_job_url is not None and not job.source_job_url.strip():
        raise PersistenceValidationError("source_job_url must not be blank")
    if job.source_job_id is None and job.source_job_url is None:
        raise PersistenceValidationError(
            "source_job_id or source_job_url is required"
        )
    if seen_at.tzinfo is None or seen_at.utcoffset() is None:
        raise PersistenceValidationError("seen_at must be timezone-aware")

    try:
        return stored_source, compute_content_hash(job), compute_dedupe_key(job)
    except ValueError as error:
        raise PersistenceValidationError(str(error)) from error


def _single_url_fallback(
    *, model: Any, company_source: CompanySourceRecord, job: NormalizedJobPosting
) -> Any | None:
    if job.source_job_url is None:
        return None
    matches = list(
        model.objects.select_for_update()
        .filter(
            company_source=company_source,
            source_job_id__isnull=True,
            source_job_url=job.source_job_url,
        )
        .order_by("pk")[:2]
    )
    if len(matches) > 1:
        raise IdentityConflictError(
            "multiple URL-fallback rows match the incoming canonical URL"
        )
    return matches[0] if matches else None


def _find_existing(
    *,
    model: Any,
    company_source: CompanySourceRecord,
    job: NormalizedJobPosting,
    dedupe_key: str,
) -> tuple[Any | None, bool]:
    if job.source_job_id is None:
        existing = (
            model.objects.select_for_update()
            .filter(company_source=company_source, dedupe_key=dedupe_key)
            .first()
        )
        return existing, False

    id_record = (
        model.objects.select_for_update()
        .filter(
            company_source=company_source,
            source_job_id=job.source_job_id,
        )
        .first()
    )
    url_record = _single_url_fallback(
        model=model,
        company_source=company_source,
        job=job,
    )
    if id_record is not None and url_record is not None and id_record.pk != url_record.pk:
        raise IdentityConflictError(
            "separate rows match the incoming source_job_id and source_job_url"
        )
    if id_record is not None:
        return id_record, False
    if url_record is not None:
        return url_record, True
    return None, False


def _create_job_posting(
    *,
    model: Any,
    company_source: CompanySourceRecord,
    job: NormalizedJobPosting,
    seen_at: datetime,
    content_hash: str,
    dedupe_key: str,
) -> Any:
    content = {field: getattr(job, field) for field in _CONTENT_FIELDS}
    return model.objects.create(
        company=company_source.company,
        company_source=company_source,
        source=job.source,
        source_job_id=job.source_job_id,
        content_hash=content_hash,
        dedupe_key=dedupe_key,
        status="active",
        first_seen_at=seen_at,
        last_seen_at=seen_at,
        consecutive_successful_misses=0,
        **content,
    )


def _update_existing(
    *,
    existing: Any,
    job: NormalizedJobPosting,
    seen_at: datetime,
    content_hash: str,
    dedupe_key: str,
    identity_upgrade: bool,
) -> PersistenceOutcome:
    update_fields: list[str] = []
    business_changed = False

    if identity_upgrade:
        existing.source_job_id = job.source_job_id
        existing.dedupe_key = dedupe_key
        update_fields.extend(("source_job_id", "dedupe_key"))
        business_changed = True

    if existing.content_hash != content_hash:
        for field in _CONTENT_FIELDS:
            setattr(existing, field, getattr(job, field))
        existing.content_hash = content_hash
        update_fields.extend((*_CONTENT_FIELDS, "content_hash"))
        business_changed = True

    if existing.status == "not_found":
        existing.status = "active"
        update_fields.append("status")
        business_changed = True

    existing.last_seen_at = seen_at
    existing.consecutive_successful_misses = 0
    update_fields.extend(("last_seen_at", "consecutive_successful_misses"))
    existing.save(update_fields=tuple(dict.fromkeys(update_fields)))

    return (
        PersistenceOutcome.UPDATED
        if business_changed
        else PersistenceOutcome.UNCHANGED
    )


def _persist_job_posting(
    *,
    company_source: CompanySourceRecord,
    job: NormalizedJobPosting,
    seen_at: datetime,
) -> PersistenceResult:
    stored_source, content_hash, dedupe_key = _validate_inputs(
        company_source=company_source,
        job=job,
        seen_at=seen_at,
    )
    model = _job_posting_model()
    existing, identity_upgrade = _find_existing(
        model=model,
        company_source=stored_source,
        job=job,
        dedupe_key=dedupe_key,
    )
    if existing is None:
        created = _create_job_posting(
            model=model,
            company_source=stored_source,
            job=job,
            seen_at=seen_at,
            content_hash=content_hash,
            dedupe_key=dedupe_key,
        )
        return PersistenceResult(
            job_posting=cast(JobPostingRecord, created),
            outcome=PersistenceOutcome.CREATED,
        )

    outcome = _update_existing(
        existing=existing,
        job=job,
        seen_at=seen_at,
        content_hash=content_hash,
        dedupe_key=dedupe_key,
        identity_upgrade=identity_upgrade,
    )
    return PersistenceResult(
        job_posting=cast(JobPostingRecord, existing),
        outcome=outcome,
    )


def persist_job_posting(
    *,
    company_source: CompanySourceRecord,
    job: NormalizedJobPosting,
    seen_at: datetime,
) -> PersistenceResult:
    """Persist one posting within an explicit CompanySource identity scope."""
    try:
        with transaction.atomic():
            return _persist_job_posting(
                company_source=company_source,
                job=job,
                seen_at=seen_at,
            )
    except IntegrityError as error:
        raise PersistenceConstraintError(
            "job persistence violated a database constraint"
        ) from error
