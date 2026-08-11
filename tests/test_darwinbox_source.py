from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import parse_qs, urlsplit

import pytest

from scraping.sources.base import SourceError
from scraping.sources.darwinbox import (
    DarwinboxMethod,
    DarwinboxSourceAdapter,
    darwinbox_source_from_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "darwinbox"


@dataclass
class CompanyStub:
    source: str = "darwinbox"
    source_jobs_url: str | None = "https://tenant.darwinbox.com/ms/candidate/careers"


class FakeTransport:
    def __init__(self, *responses: str | bytes | Exception) -> None:
        self.responses = iter(responses)
        self.calls: list[tuple[DarwinboxMethod, str, dict[str, object] | None, float]] = []

    def __call__(
        self,
        method: DarwinboxMethod,
        url: str,
        json_body: object,
        timeout_seconds: float,
    ) -> str | bytes:
        body = cast(dict[str, object] | None, json_body)
        self.calls.append((method, url, body, timeout_seconds))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def listing(*jobs: dict[str, object], total: int = 0) -> str:
    return json.dumps({"status": "success", "data": jobs, "job_counts": total})


def job(job_id: str, *, jd: str = "Description", title: str = "Analyst") -> dict[str, object]:
    return {"id": job_id, "title": title, "jd": jd}


def test_legacy_url_uses_observed_main_company_identifier() -> None:
    result = darwinbox_source_from_url(
        "  https://acuitykp.darwinbox.com/ms/candidate/careers  "
    )

    assert result.scheme == "https"
    assert result.host == "acuitykp.darwinbox.com"
    assert result.company_id == "main"


def test_candidate_v2_url_uses_path_company_identifier() -> None:
    result = darwinbox_source_from_url(
        "http://tenant.darwinbox.com/ms/candidatev2/acme/careers/jobDetails/123"
    )

    assert result.scheme == "http"
    assert result.company_id == "acme"


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "https://example.com/ms/candidate/careers",
        "https://darwinbox.com/ms/candidate/careers",
        "https://tenant.darwinbox.com/not-careers",
        "https://user@tenant.darwinbox.com/ms/candidate/careers",
        "https://tenant.darwinbox.com:443/ms/candidate/careers",
        "https://tenant.darwinbox.com/ms/candidatev2/%20/careers",
    ],
)
def test_url_parser_rejects_unsupported_or_unsafe_urls(url: str | None) -> None:
    with pytest.raises(SourceError):
        darwinbox_source_from_url(url)


def test_one_fetch_walks_two_pages_and_maps_records() -> None:
    transport = FakeTransport(
        fixture("page_1.json"), fixture("page_2_terminal.json")
    )

    batch = DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert [record["source_job_id"] for record in batch.records] == [
        "darwinbox-1",
        "darwinbox-2",
        "darwinbox-3",
    ]
    assert batch.requests_made == 2
    assert [call[0] for call in transport.calls] == ["POST", "POST"]
    assert [call[2] for call in transport.calls] == [
        {"companyId": "main", "page": 1, "sort_option": "new", "limit": 10},
        {"companyId": "main", "page": 2, "sort_option": "new", "limit": 10},
    ]
    first = batch.records[0]
    assert first["source"] == "darwinbox"
    assert first["title"] == "Data Analyst"
    assert first["location"] == "Bengaluru"
    assert first["country"] == "India"
    assert first["job_function"] == "Analytics"
    assert first["employment_type"] == "Full time"
    assert first["published_at"] == "2026-08-01T09:00:00+00:00"
    assert first["description"] == "<p>Build trusted reports.</p>"
    assert first["source_job_url"] == (
        "https://tenant.darwinbox.com/ms/candidatev2/main/careers/"
        "jobDetails/darwinbox-1?from=all"
    )
    assert batch.records[1]["location"] == "Gurugram, Remote"


def test_exact_code_pagination_walks_page_one_then_page_two_once() -> None:
    transport = FakeTransport(
        listing(job("A"), job("B"), total=4),
        listing(job("C"), job("D"), total=4),
    )

    batch = DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert [record["source_job_id"] for record in batch.records] == ["A", "B", "C", "D"]
    assert [call[2] for call in transport.calls] == [
        {"companyId": "main", "page": 1, "sort_option": "new", "limit": 10},
        {"companyId": "main", "page": 2, "sort_option": "new", "limit": 10},
    ]


def test_one_page_and_three_page_complete_snapshots() -> None:
    one_page = DarwinboxSourceAdapter(
        request=FakeTransport(listing(job("A"), total=1))
    ).fetch(company=CompanyStub())
    three_page_transport = FakeTransport(
        listing(job("A"), total=3),
        listing(job("B"), total=3),
        listing(job("C"), total=3),
    )
    three_pages = DarwinboxSourceAdapter(request=three_page_transport).fetch(
        company=CompanyStub()
    )

    assert len(one_page.records) == 1
    assert len(three_pages.records) == 3
    assert three_pages.requests_made == 3


def test_valid_empty_snapshot_is_complete() -> None:
    transport = FakeTransport(fixture("empty_complete.json"))

    batch = DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert batch.records == ()
    assert batch.requests_made == 1


def test_duplicate_across_pages_is_deduplicated_when_page_has_new_id() -> None:
    transport = FakeTransport(
        listing(job("A"), job("B"), total=3),
        listing(job("B"), job("C"), total=3),
    )

    batch = DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert [record["source_job_id"] for record in batch.records] == ["A", "B", "C"]


def test_duplicate_inside_one_page_is_deduplicated() -> None:
    transport = FakeTransport(listing(job("A"), job("A"), total=1))

    batch = DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert [record["source_job_id"] for record in batch.records] == ["A"]


@pytest.mark.parametrize(
    ("responses", "message", "expected_requests"),
    [
        ((listing(job("A"), total=2), listing(job("A"), total=2)), "repeated", 2),
        ((listing(job("A"), total=2), listing(total=2)), "empty page", 2),
        ((fixture("malformed_listing.json"),), "job_counts", 1),
        ((json.dumps({"status": "error", "data": [], "job_counts": 0}),), "status", 1),
        (("<html><title>Login</title></html>",), "valid JSON", 1),
        ((listing(job("A"), total=2), listing(job("B"), total=3)), "changed", 2),
        ((listing(job("A"), job("B"), total=1),), "more unique", 1),
    ],
)
def test_incomplete_or_malformed_listing_fails_closed(
    responses: tuple[str, ...], message: str, expected_requests: int
) -> None:
    transport = FakeTransport(*responses)

    with pytest.raises(SourceError, match=message) as caught:
        DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert caught.value.requests_made == expected_requests


def test_page_limit_before_total_fails_closed() -> None:
    transport = FakeTransport(listing(job("A"), total=100))

    with pytest.raises(SourceError, match="page limit") as caught:
        DarwinboxSourceAdapter(request=transport, max_pages=1).fetch(
            company=CompanyStub()
        )

    assert caught.value.requests_made == 1


def test_transport_failure_preserves_request_count() -> None:
    with pytest.raises(SourceError, match="request failed") as caught:
        DarwinboxSourceAdapter(request=FakeTransport(RuntimeError("offline"))).fetch(
            company=CompanyStub()
        )

    assert caught.value.requests_made == 1


def test_detail_is_not_requested_when_listing_description_is_present() -> None:
    transport = FakeTransport(listing(job("A", jd="Present"), total=1))

    batch = DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert batch.requests_made == 1
    assert [call[0] for call in transport.calls] == ["POST"]


def test_empty_listing_description_fetches_only_matching_detail() -> None:
    detail = fixture("detail_success.json")
    listing_job = job("darwinbox-detail", jd="", title="Detail Analyst")
    transport = FakeTransport(listing(listing_job, total=1), detail)

    batch = DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert batch.requests_made == 2
    assert [call[0] for call in transport.calls] == ["POST", "GET"]
    assert transport.calls[1][2] is None
    assert urlsplit(transport.calls[1][1]).path.endswith("/job/darwinbox-detail")
    assert parse_qs(urlsplit(transport.calls[1][1]).query) == {"companyId": ["main"]}
    assert batch.records[0]["description"] == "<p>Detailed description.</p>"
    assert batch.records[0]["job_function"] == "Research"


@pytest.mark.parametrize(
    ("fixture_name", "message"),
    [
        ("detail_malformed.json", "non-empty array"),
        ("detail_id_mismatch.json", "does not match"),
    ],
)
def test_malformed_or_mismatched_detail_fails_closed(
    fixture_name: str, message: str
) -> None:
    transport = FakeTransport(
        listing(job("darwinbox-detail", jd=""), total=1), fixture(fixture_name)
    )

    with pytest.raises(SourceError, match=message) as caught:
        DarwinboxSourceAdapter(request=transport).fetch(company=CompanyStub())

    assert caught.value.requests_made == 2


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"page_size": 0}, "page_size"),
        ({"page_size": True}, "page_size"),
        ({"timeout_seconds": 0}, "timeout_seconds"),
        ({"timeout_seconds": True}, "timeout_seconds"),
        ({"max_pages": 0}, "max_pages"),
    ],
)
def test_invalid_adapter_configuration_is_rejected(
    kwargs: dict[str, object], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        DarwinboxSourceAdapter(**kwargs)  # type: ignore[arg-type]
