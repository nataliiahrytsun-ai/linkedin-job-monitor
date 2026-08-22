from __future__ import annotations

from dataclasses import FrozenInstanceError, fields
from datetime import UTC, datetime, timedelta, timezone

import pytest

from scraping.normalization import (
    NormalizedJobPosting,
    canonicalize_source_job_url,
    normalize_job_posting,
)


def minimal_job(**overrides: object) -> NormalizedJobPosting:
    values: dict[str, object] = {"source": "feed", "source_job_id": "job-1"}
    values.update(overrides)
    return normalize_job_posting(**values)  # type: ignore[arg-type]


def test_dto_is_immutable_and_contains_only_normalized_content_fields() -> None:
    job = minimal_job()

    with pytest.raises(FrozenInstanceError):
        job.title = "Changed"  # type: ignore[misc]

    assert not hasattr(job, "__dict__")
    assert {field.name for field in fields(job)} == {
        "source",
        "source_job_id",
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
    }


def test_source_and_short_text_are_normalized_without_mutating_inputs() -> None:
    raw: dict[str, object] = {
        "source": "  CUSTOMER_FEED  ",
        "source_job_id": "  external  id  ",
        "title": "  Senior\t Delivery   Manager ",
        "location": " Vienna\n Austria ",
    }
    original = raw.copy()

    job = normalize_job_posting(**raw)  # type: ignore[arg-type]

    assert job.source == "customer_feed"
    assert job.source_job_id == "external  id"
    assert job.title == "Senior Delivery Manager"
    assert job.location == "Vienna Austria"
    assert raw == original


def test_empty_optional_strings_become_none() -> None:
    job = minimal_job(
        title=" \t ",
        country="",
        description="\r\n  \r\n",
        source_job_url=" ",
    )

    assert job.title is None
    assert job.country is None
    assert job.description is None
    assert job.source_job_url is None


def test_description_normalizes_newlines_and_trailing_whitespace() -> None:
    first = minimal_job(description="  First line  \r\n\r  Second line\t \r\n")
    second = minimal_job(description="First line\n\nSecond line")

    assert first.description == "First line\n\nSecond line"
    assert first.description == second.description


@pytest.mark.parametrize("value", ["remote", " HYBRID ", "OnSite", None, ""])
def test_workplace_type_accepts_only_canonical_values_or_empty(
    value: str | None,
) -> None:
    job = minimal_job(workplace_type=value)

    expected = value.strip().lower() or None if value is not None else None
    assert job.workplace_type == expected


def test_unknown_workplace_type_is_rejected() -> None:
    with pytest.raises(ValueError, match="workplace_type"):
        minimal_job(workplace_type="field")


def test_url_canonicalization_normalizes_authority_and_preserves_path_query() -> None:
    url = canonicalize_source_job_url(
        "HTTPS://Jobs.Example.COM:443/Openings/One?utm_source=test&ref=ABC#details"
    )

    assert url == "https://jobs.example.com/Openings/One?utm_source=test&ref=ABC"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://EXAMPLE.com:80/jobs", "http://example.com/jobs"),
        ("https://EXAMPLE.com:444/jobs", "https://example.com:444/jobs"),
    ],
)
def test_url_canonicalization_removes_only_default_ports(raw: str, expected: str) -> None:
    assert canonicalize_source_job_url(raw) == expected


@pytest.mark.parametrize(
    "value",
    ["jobs.example.com/one", "/jobs/one", "ftp://jobs.example.com/one", "https://:443/x"],
)
def test_invalid_url_is_rejected(value: str) -> None:
    with pytest.raises(ValueError, match="HTTP"):
        canonicalize_source_job_url(value)


def test_naive_datetime_is_explicitly_interpreted_as_utc() -> None:
    published_at = datetime(2026, 8, 6, 10, 30)

    assert minimal_job(published_at=published_at).published_at == datetime(
        2026, 8, 6, 10, 30, tzinfo=UTC
    )


def test_aware_datetime_is_converted_to_utc() -> None:
    source_timezone = timezone(timedelta(hours=2))
    published_at = datetime(2026, 8, 6, 12, 30, tzinfo=source_timezone)

    assert minimal_job(published_at=published_at).published_at == datetime(
        2026, 8, 6, 10, 30, tzinfo=UTC
    )


def test_missing_optional_fields_remain_none() -> None:
    job = minimal_job()

    optional_fields = (
        "title",
        "country",
        "city",
        "location",
        "workplace_type",
        "employment_type",
        "seniority_level",
        "job_function",
        "industry",
        "published_at",
        "description",
        "source_job_url",
    )
    assert all(getattr(job, name) is None for name in optional_fields)


def test_source_must_not_be_empty() -> None:
    with pytest.raises(ValueError, match="source must not be empty"):
        normalize_job_posting(source=" ", source_job_id="job-1")


def test_source_id_or_url_is_required() -> None:
    with pytest.raises(ValueError, match="source_job_id or source_job_url"):
        normalize_job_posting(source="feed", source_job_id=" ", source_job_url=" ")


def test_canonical_url_can_supply_identity_without_source_id() -> None:
    job = normalize_job_posting(
        source="feed",
        source_job_url="HTTPS://JOBS.EXAMPLE.COM:443/openings/1#apply",
    )

    assert job.source_job_id is None
    assert job.source_job_url == "https://jobs.example.com/openings/1"
