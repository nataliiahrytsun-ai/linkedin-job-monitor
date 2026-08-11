from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from scraping.sources.base import SourceBatch, SourceError
from scraping.sources.lever import (
    DEFAULT_MAX_PAGES,
    DEFAULT_PAGE_SIZE,
    DEFAULT_TIMEOUT_SECONDS,
    LeverSourceAdapter,
    lever_site_from_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "lever"


@dataclass
class CompanyStub:
    source_jobs_url: str | None = "https://jobs.lever.co/olo"
    source: str = "lever"


@dataclass
class FakeHttpGet:
    responses: Iterator[str | bytes | Exception]
    calls: list[tuple[str, float]] = field(default_factory=list)

    def __call__(self, url: str, timeout_seconds: float) -> str | bytes:
        self.calls.append((url, timeout_seconds))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fake_http(*responses: str | bytes | Exception) -> FakeHttpGet:
    return FakeHttpGet(iter(responses))


def fetch_json_page(
    page: object,
    *,
    page_size: int = 100,
    max_pages: int = 100,
) -> tuple[FakeHttpGet, SourceBatch]:
    http_get = fake_http(json.dumps(page))
    adapter = LeverSourceAdapter(
        http_get=http_get,
        page_size=page_size,
        max_pages=max_pages,
    )
    return http_get, adapter.fetch(company=CompanyStub())


def test_valid_company_url_returns_site_slug() -> None:
    assert lever_site_from_url("https://jobs.lever.co/olo") == "olo"


def test_company_url_allows_trailing_slash_and_ignores_query_fragment() -> None:
    assert lever_site_from_url("https://jobs.lever.co/olo/?view=all#jobs") == "olo"


def test_company_url_rejects_invalid_scheme() -> None:
    with pytest.raises(SourceError, match="https") as caught:
        lever_site_from_url("http://jobs.lever.co/olo")
    assert caught.value.requests_made == 0


def test_company_url_rejects_invalid_host() -> None:
    with pytest.raises(SourceError, match="jobs.lever.co"):
        lever_site_from_url("https://example.com/olo")


def test_company_url_rejects_extra_path_segments() -> None:
    with pytest.raises(SourceError, match="one site slug"):
        lever_site_from_url("https://jobs.lever.co/olo/jobs")


@pytest.mark.parametrize("source_jobs_url", [None, "", "   "])
def test_company_url_rejects_missing_url(source_jobs_url: str | None) -> None:
    with pytest.raises(SourceError, match="missing") as caught:
        lever_site_from_url(source_jobs_url)
    assert caught.value.requests_made == 0


def test_full_posting_mapping_uses_source_neutral_fields() -> None:
    _, batch = fetch_json_page(json.loads(fixture_text("postings_page_1.json")))

    assert batch.records[0] == {
        "source": "lever",
        "source_job_id": "lever-1",
        "source_job_url": "https://jobs.lever.co/example/lever-1",
        "title": "Senior Data Engineer",
        "location": "Vienna, Austria",
        "country": "Austria",
        "city": None,
        "workplace_type": "hybrid",
        "employment_type": "Full-time",
        "published_at": "2026-08-11T08:02:00+00:00",
        "description": "Build reliable data products.\n\nWork with a distributed team.",
        "job_function": "Engineering",
        "seniority_level": None,
        "industry": None,
    }


def test_nullable_fields_are_returned_as_none() -> None:
    _, batch = fetch_json_page([{"id": "minimal"}])

    assert batch.records == (
        {
            "source": "lever",
            "source_job_id": "minimal",
            "source_job_url": None,
            "title": None,
            "location": None,
            "country": None,
            "city": None,
            "workplace_type": None,
            "employment_type": None,
            "published_at": None,
            "description": None,
            "job_function": None,
            "seniority_level": None,
            "industry": None,
        },
    )


def test_location_falls_back_to_all_locations_and_function_to_team() -> None:
    _, batch = fetch_json_page(json.loads(fixture_text("postings_page_1.json")))

    second = batch.records[1]
    assert second["location"] == "Remote - Europe, Remote - UK"
    assert second["job_function"] == "Customer Success"


@pytest.mark.parametrize(
    ("lever_value", "expected"),
    [
        ("remote", "remote"),
        ("HYBRID", "hybrid"),
        ("on-site", "onsite"),
        ("onsite", "onsite"),
        ("office", None),
        (None, None),
    ],
)
def test_workplace_mapping(lever_value: str | None, expected: str | None) -> None:
    posting: dict[str, object] = {"id": "workplace"}
    if lever_value is not None:
        posting["workplaceType"] = lever_value

    _, batch = fetch_json_page([posting])

    assert batch.records[0]["workplace_type"] == expected


def test_created_at_integer_milliseconds_becomes_iso_utc() -> None:
    _, batch = fetch_json_page([{"id": "dated", "createdAt": 0}])

    assert batch.records[0]["published_at"] == "1970-01-01T00:00:00+00:00"


def test_pagination_uses_limit_and_accumulated_skip() -> None:
    http_get = fake_http(
        fixture_text("postings_page_1.json"),
        fixture_text("postings_page_2.json"),
    )
    adapter = LeverSourceAdapter(http_get=http_get, page_size=2)

    batch = adapter.fetch(company=CompanyStub())

    assert [record["source_job_id"] for record in batch.records] == [
        "lever-1",
        "lever-2",
        "lever-3",
    ]
    queries = [parse_qs(urlsplit(url).query) for url, _timeout in http_get.calls]
    assert queries == [
        {"mode": ["json"], "limit": ["2"], "skip": ["0"]},
        {"mode": ["json"], "limit": ["2"], "skip": ["2"]},
    ]
    assert urlsplit(http_get.calls[0][0]).path == "/v0/postings/olo"


def test_empty_page_terminates_snapshot() -> None:
    http_get = fake_http(fixture_text("postings_empty.json"))

    batch = LeverSourceAdapter(http_get=http_get, page_size=2).fetch(
        company=CompanyStub()
    )

    assert batch.records == ()
    assert batch.requests_made == 1
    assert len(http_get.calls) == 1


def test_short_page_terminates_without_extra_request() -> None:
    http_get = fake_http(fixture_text("postings_page_2.json"))

    batch = LeverSourceAdapter(http_get=http_get, page_size=2).fetch(
        company=CompanyStub()
    )

    assert len(batch.records) == 1
    assert batch.requests_made == 1
    assert len(http_get.calls) == 1


def test_full_duplicate_page_raises_instead_of_looping_or_returning_partial() -> None:
    http_get = fake_http(
        fixture_text("postings_page_1.json"),
        fixture_text("postings_duplicate.json"),
    )

    with pytest.raises(SourceError, match="no new job IDs") as caught:
        LeverSourceAdapter(http_get=http_get, page_size=2).fetch(company=CompanyStub())

    assert caught.value.requests_made == 2
    assert len(http_get.calls) == 2


def test_duplicate_ids_within_a_page_are_deduplicated_in_first_seen_order() -> None:
    page = [{"id": "one", "text": "First"}, {"id": "one", "text": "Changed"}]
    _, batch = fetch_json_page(page, page_size=3)

    assert len(batch.records) == 1
    assert batch.records[0]["title"] == "First"


def test_requests_made_counts_each_successful_http_attempt() -> None:
    http_get = fake_http(
        fixture_text("postings_page_1.json"),
        fixture_text("postings_empty.json"),
    )

    batch = LeverSourceAdapter(http_get=http_get, page_size=2).fetch(
        company=CompanyStub()
    )

    assert batch.requests_made == 2


def test_http_failure_counts_the_single_attempt_and_does_not_retry() -> None:
    http_get = fake_http(TimeoutError("offline fake timeout"))

    with pytest.raises(SourceError, match="request failed") as caught:
        LeverSourceAdapter(http_get=http_get).fetch(company=CompanyStub())

    assert caught.value.requests_made == 1
    assert len(http_get.calls) == 1


@pytest.mark.parametrize(
    "body",
    [
        "not-json",
        pytest.param(
            fixture_text("postings_malformed.json"),
            id="non-array-root",
        ),
        json.dumps(["not-an-object"]),
        json.dumps([{"id": ""}]),
        json.dumps([{"id": "valid", "categories": []}]),
        json.dumps([{"id": "valid", "createdAt": True}]),
    ],
)
def test_malformed_json_or_schema_raises_source_error(body: str) -> None:
    http_get = fake_http(body)

    with pytest.raises(SourceError) as caught:
        LeverSourceAdapter(http_get=http_get).fetch(company=CompanyStub())

    assert caught.value.requests_made == 1


def test_safety_page_limit_fails_instead_of_returning_partial_snapshot() -> None:
    http_get = fake_http(fixture_text("postings_page_1.json"))

    with pytest.raises(SourceError, match="safety page limit") as caught:
        LeverSourceAdapter(http_get=http_get, page_size=2, max_pages=1).fetch(
            company=CompanyStub()
        )

    assert caught.value.requests_made == 1


def test_source_is_always_lever_regardless_of_company_source() -> None:
    _, batch = fetch_json_page([{"id": "source-check"}])

    assert batch.records[0]["source"] == "lever"


def test_injected_http_receives_configured_timeout_without_real_network() -> None:
    http_get = fake_http(fixture_text("postings_empty.json"))

    LeverSourceAdapter(http_get=http_get, timeout_seconds=7.5).fetch(
        company=CompanyStub()
    )

    assert http_get.calls[0][1] == 7.5


def test_production_defaults_are_not_demo_pagination_values() -> None:
    assert DEFAULT_PAGE_SIZE == 100
    assert DEFAULT_TIMEOUT_SECONDS == 20.0
    assert DEFAULT_MAX_PAGES == 100


@pytest.mark.parametrize(
    "kwargs",
    [
        {"page_size": 0},
        {"page_size": True},
        {"timeout_seconds": 0},
        {"timeout_seconds": True},
        {"max_pages": 0},
        {"max_pages": True},
    ],
)
def test_invalid_adapter_limits_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        LeverSourceAdapter(http_get=fake_http(), **kwargs)  # type: ignore[arg-type]
