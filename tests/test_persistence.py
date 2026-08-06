from __future__ import annotations

import importlib
import os
from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest

from scraping.identity import compute_content_hash, compute_dedupe_key
from scraping.normalization import NormalizedJobPosting, normalize_job_posting
from scraping.persistence import (
    IdentityConflictError,
    PersistenceConstraintError,
    PersistenceOutcome,
    PersistenceValidationError,
    persist_job_posting,
)


@pytest.fixture(scope="module", autouse=True)
def migrated_database(tmp_path_factory: pytest.TempPathFactory) -> None:
    apps = importlib.import_module("django.apps").apps
    if not apps.ready:
        database_path = tmp_path_factory.mktemp("persistence-db") / "persistence.sqlite3"
        os.environ["DJANGO_SETTINGS_MODULE"] = "job_monitor.settings"
        os.environ["JOB_MONITOR_SQLITE_PATH"] = str(database_path)
        django = importlib.import_module("django")
        django.setup()
    management = importlib.import_module("django.core.management")
    management.call_command("migrate", interactive=False, verbosity=0)


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    management = importlib.import_module("django.core.management")
    management.call_command("flush", interactive=False, verbosity=0)


def model(name: str) -> Any:
    return importlib.import_module("django.apps").apps.get_model(name)


def create_company(*, name: str = "Example", source: str = "feed") -> Any:
    return model("companies.Company").objects.create(name=name, source=source)


def normalized_job(**overrides: object) -> NormalizedJobPosting:
    values: dict[str, object] = {
        "source": "feed",
        "source_job_id": "job-1",
        "title": "Delivery Manager",
        "country": "Austria",
        "city": "Vienna",
        "location": "Vienna, Austria",
        "workplace_type": "hybrid",
        "employment_type": "full-time",
        "seniority_level": "mid-senior",
        "job_function": "project management",
        "industry": "analytics",
        "published_at": datetime(2026, 8, 1, 9, tzinfo=UTC),
        "description": "Deliver customer projects.",
        "source_job_url": "https://jobs.example.com/openings/1",
    }
    values.update(overrides)
    return normalize_job_posting(**values)  # type: ignore[arg-type]


def observed_at(hour: int = 10) -> datetime:
    return datetime(2026, 8, 6, hour, tzinfo=UTC)


def test_create_new_job_sets_identity_content_and_seen_timestamps() -> None:
    company = create_company()
    job = normalized_job()

    result = persist_job_posting(company=company, job=job, seen_at=observed_at())

    stored = result.job_posting
    assert result.outcome is PersistenceOutcome.CREATED
    assert model("jobs.JobPosting").objects.count() == 1
    assert stored.company.pk == company.pk
    assert stored.source == "feed"
    assert stored.source_job_id == "job-1"
    assert stored.title == "Delivery Manager"
    assert stored.content_hash == compute_content_hash(job)
    assert stored.dedupe_key == compute_dedupe_key(job)
    assert stored.status == "active"
    assert stored.first_seen_at == observed_at()
    assert stored.last_seen_at == observed_at()
    assert stored.consecutive_successful_misses == 0


def test_repeated_identical_job_is_unchanged_without_duplicate() -> None:
    company = create_company()
    job = normalized_job()
    first = persist_job_posting(company=company, job=job, seen_at=observed_at())

    second = persist_job_posting(company=company, job=job, seen_at=observed_at(11))

    assert second.outcome is PersistenceOutcome.UNCHANGED
    assert second.job_posting.pk == first.job_posting.pk
    assert second.job_posting.first_seen_at == observed_at()
    assert second.job_posting.last_seen_at == observed_at(11)
    assert model("jobs.JobPosting").objects.count() == 1


def test_meaningful_content_change_updates_all_content_but_not_first_seen() -> None:
    company = create_company()
    original = persist_job_posting(
        company=company, job=normalized_job(), seen_at=observed_at()
    ).job_posting
    changed = normalized_job(
        title="Programme Director",
        country="Germany",
        city="Berlin",
        location="Berlin, Germany",
        workplace_type="remote",
        employment_type="contract",
        seniority_level="director",
        job_function="operations",
        industry="technology",
        published_at=datetime(2026, 8, 2, 8, tzinfo=UTC),
        description="Lead delivery operations.",
        source_job_url="https://jobs.example.com/openings/1?lang=de",
    )

    result = persist_job_posting(
        company=company, job=changed, seen_at=observed_at(12)
    )

    stored = result.job_posting
    assert result.outcome is PersistenceOutcome.UPDATED
    assert stored.pk == original.pk
    assert stored.first_seen_at == observed_at()
    assert stored.last_seen_at == observed_at(12)
    assert stored.title == changed.title
    assert stored.country == changed.country
    assert stored.city == changed.city
    assert stored.location == changed.location
    assert stored.workplace_type == changed.workplace_type
    assert stored.employment_type == changed.employment_type
    assert stored.seniority_level == changed.seniority_level
    assert stored.job_function == changed.job_function
    assert stored.industry == changed.industry
    assert stored.published_at == changed.published_at
    assert stored.description == changed.description
    assert stored.source_job_url == changed.source_job_url
    assert stored.content_hash == compute_content_hash(changed)


def test_repeat_resets_miss_counter_but_remains_unchanged() -> None:
    company = create_company()
    result = persist_job_posting(
        company=company, job=normalized_job(), seen_at=observed_at()
    )
    stored = model("jobs.JobPosting").objects.get(pk=result.job_posting.pk)
    stored.consecutive_successful_misses = 2
    stored.save(update_fields=("consecutive_successful_misses",))

    repeated = persist_job_posting(
        company=company, job=normalized_job(), seen_at=observed_at(11)
    )

    assert repeated.outcome is PersistenceOutcome.UNCHANGED
    assert repeated.job_posting.consecutive_successful_misses == 0


def test_not_found_reactivates_but_closed_does_not() -> None:
    company = create_company()
    job = normalized_job()
    not_found = persist_job_posting(
        company=company, job=job, seen_at=observed_at()
    ).job_posting
    stored = model("jobs.JobPosting").objects.get(pk=not_found.pk)
    stored.status = "not_found"
    stored.save(update_fields=("status",))

    reactivated = persist_job_posting(
        company=company, job=job, seen_at=observed_at(11)
    )
    assert reactivated.outcome is PersistenceOutcome.UPDATED
    assert reactivated.job_posting.status == "active"

    closed = model("jobs.JobPosting").objects.get(pk=not_found.pk)
    closed.status = "closed"
    closed.save(update_fields=("status",))
    observed_closed = persist_job_posting(
        company=company, job=job, seen_at=observed_at(12)
    )
    assert observed_closed.outcome is PersistenceOutcome.UNCHANGED
    assert observed_closed.job_posting.status == "closed"


def test_id_first_lookup_survives_url_change() -> None:
    company = create_company()
    first = persist_job_posting(
        company=company, job=normalized_job(), seen_at=observed_at()
    )
    changed_url = normalized_job(
        source_job_url="https://jobs.example.com/canonical/job-1"
    )

    second = persist_job_posting(
        company=company, job=changed_url, seen_at=observed_at(11)
    )

    assert second.outcome is PersistenceOutcome.UPDATED
    assert second.job_posting.pk == first.job_posting.pk
    assert second.job_posting.source_job_url == changed_url.source_job_url
    assert model("jobs.JobPosting").objects.count() == 1


def test_url_fallback_lookup_is_unchanged() -> None:
    company = create_company()
    url_job = normalized_job(source_job_id=None)
    first = persist_job_posting(
        company=company, job=url_job, seen_at=observed_at()
    )

    second = persist_job_posting(
        company=company, job=url_job, seen_at=observed_at(11)
    )

    assert second.outcome is PersistenceOutcome.UNCHANGED
    assert second.job_posting.pk == first.job_posting.pk
    assert model("jobs.JobPosting").objects.count() == 1


def test_url_only_record_is_upgraded_to_source_id_without_duplicate() -> None:
    company = create_company()
    url_only_job = normalized_job(source_job_id=None)
    first = persist_job_posting(
        company=company, job=url_only_job, seen_at=observed_at()
    )
    old_key = first.job_posting.dedupe_key
    identified_job = normalized_job(source_job_id="external-42")

    upgraded = persist_job_posting(
        company=company, job=identified_job, seen_at=observed_at(11)
    )

    assert upgraded.outcome is PersistenceOutcome.UPDATED
    assert upgraded.job_posting.pk == first.job_posting.pk
    assert upgraded.job_posting.source_job_id == "external-42"
    assert upgraded.job_posting.dedupe_key == compute_dedupe_key(identified_job)
    assert upgraded.job_posting.dedupe_key != old_key
    assert model("jobs.JobPosting").objects.count() == 1


def test_separate_id_and_url_rows_raise_identity_conflict() -> None:
    company = create_company()
    identified_job = normalized_job(source_job_id="external-42")
    id_result = persist_job_posting(
        company=company, job=identified_job, seen_at=observed_at()
    )
    url_result = persist_job_posting(
        company=company,
        job=normalized_job(source_job_id=None),
        seen_at=observed_at(11),
    )

    with pytest.raises(IdentityConflictError, match="separate rows"):
        persist_job_posting(
            company=company,
            job=identified_job,
            seen_at=observed_at(12),
        )

    assert model("jobs.JobPosting").objects.count() == 2
    assert id_result.job_posting.pk != url_result.job_posting.pk


def test_same_source_job_id_is_independent_between_companies() -> None:
    first_company = create_company(name="One")
    second_company = create_company(name="Two")

    first = persist_job_posting(
        company=first_company, job=normalized_job(), seen_at=observed_at()
    )
    second = persist_job_posting(
        company=second_company, job=normalized_job(), seen_at=observed_at()
    )

    assert first.job_posting.pk != second.job_posting.pk
    assert model("jobs.JobPosting").objects.count() == 2


def test_different_sources_are_not_mixed() -> None:
    first_company = create_company(name="One", source="feed_one")
    second_company = create_company(name="Two", source="feed_two")

    first = persist_job_posting(
        company=first_company,
        job=normalized_job(source="feed_one"),
        seen_at=observed_at(),
    )
    second = persist_job_posting(
        company=second_company,
        job=normalized_job(source="feed_two"),
        seen_at=observed_at(),
    )

    assert first.job_posting.pk != second.job_posting.pk
    assert first.job_posting.dedupe_key != second.job_posting.dedupe_key


def test_company_source_mismatch_is_rejected() -> None:
    company = create_company(source="feed")

    with pytest.raises(PersistenceValidationError, match="match company.source"):
        persist_job_posting(
            company=company,
            job=normalized_job(source="other"),
            seen_at=observed_at(),
        )

    assert model("jobs.JobPosting").objects.count() == 0


def test_naive_seen_at_is_rejected() -> None:
    with pytest.raises(PersistenceValidationError, match="timezone-aware"):
        persist_job_posting(
            company=create_company(),
            job=normalized_job(),
            seen_at=datetime(2026, 8, 6, 10),
        )


def test_unsaved_company_is_rejected() -> None:
    company = model("companies.Company")(name="Unsaved", source="feed")

    with pytest.raises(PersistenceValidationError, match="already be saved"):
        persist_job_posting(
            company=company,
            job=normalized_job(),
            seen_at=observed_at(),
        )


def test_constraint_error_rolls_back_identity_upgrade() -> None:
    company = create_company()
    url_only_job = normalized_job(source_job_id=None)
    fallback = persist_job_posting(
        company=company, job=url_only_job, seen_at=observed_at()
    ).job_posting
    incoming = normalized_job(source_job_id="new-id")
    colliding_key = compute_dedupe_key(incoming)
    model("jobs.JobPosting").objects.create(
        company=company,
        source="feed",
        source_job_id="other-id",
        source_job_url="https://jobs.example.com/other",
        content_hash="f" * 64,
        dedupe_key=colliding_key,
    )

    with pytest.raises(PersistenceConstraintError, match="constraint"):
        persist_job_posting(
            company=company,
            job=incoming,
            seen_at=observed_at(11),
        )

    stored_fallback = model("jobs.JobPosting").objects.get(pk=fallback.pk)
    assert stored_fallback.source_job_id is None
    assert stored_fallback.dedupe_key == compute_dedupe_key(url_only_job)


def test_input_dto_remains_immutable_and_unchanged() -> None:
    company = create_company()
    job = normalized_job()

    persist_job_posting(company=company, job=job, seen_at=observed_at())

    assert job == normalized_job()
    with pytest.raises(FrozenInstanceError):
        job.title = "Changed"  # type: ignore[misc]


def test_seen_at_with_non_utc_timezone_is_accepted() -> None:
    company = create_company()
    seen_at = datetime(
        2026,
        8,
        6,
        12,
        tzinfo=timezone(timedelta(hours=2)),
    )

    result = persist_job_posting(company=company, job=normalized_job(), seen_at=seen_at)

    assert result.job_posting.last_seen_at == seen_at
