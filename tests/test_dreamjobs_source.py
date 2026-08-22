from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import pytest
from lxml import html as lxml_html  # type: ignore[import-untyped]

from scraping.sources.base import SourceError
from scraping.sources.dreamjobs import (
    DEFAULT_TIMEOUT_SECONDS,
    DREAMJOBS_API_URL,
    DreamJobsSourceAdapter,
    dreamjobs_source_from_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "dreamjobs"


@dataclass
class CompanyStub:
    source: str = "dreamjobs"
    source_jobs_url: str | None = "https://careers.example.test/jobs"


@dataclass
class FakeHttpRequest:
    responses: Iterator[str | bytes | Exception]
    calls: list[tuple[str, str, Mapping[str, str], Mapping[str, object] | None, float]] = field(
        default_factory=list
    )

    def __call__(
        self,
        method: str,
        url: str,
        headers: Mapping[str, str],
        payload: Mapping[str, object] | None,
        timeout: float,
    ) -> str | bytes:
        self.calls.append((method, url, headers, payload, timeout))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fake_http(*responses: str | bytes | Exception) -> FakeHttpRequest:
    return FakeHttpRequest(iter(responses))


def first_page_only() -> str:
    document = lxml_html.fromstring(fixture("listing.html"))
    script = document.xpath('//script[@id="__NEXT_DATA__"]')[0]
    data = json.loads(script.text)
    listing = data["props"]["pageProps"]["dehydratedState"]["queries"][1]["state"]["data"][
        "opportunities"
    ]
    listing["opportunities"] = listing["opportunities"][:1]
    data["props"]["pageProps"]["dehydratedState"]["queries"][1]["queryKey"][1]["pagination"][
        "items"
    ] = 1
    script.text = json.dumps(data)
    return cast(str, lxml_html.tostring(document, encoding="unicode"))


@pytest.mark.parametrize(
    "url",
    [
        "https://careers.datasentics.com/jobs",
        "https://careers.datasentics.com/jobs/",
        "https://careers.datasentics.com/jobs?activeOpportunityId=25520&page=2#job",
    ],
)
def test_supported_custom_domain_urls_canonicalize(url: str) -> None:
    source = dreamjobs_source_from_url(url)

    assert source.host == "careers.datasentics.com"
    assert source.listing_url == "https://careers.datasentics.com/jobs"


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "http://careers.example.test/jobs",
        "https://careers.example.test/",
        "https://careers.example.test/careers",
        "https://user@careers.example.test/jobs",
        "https://careers.example.test:443/jobs",
    ],
)
def test_invalid_source_urls_fail_closed(url: str | None) -> None:
    with pytest.raises(SourceError):
        dreamjobs_source_from_url(url)


def test_complete_embedded_snapshot_maps_details_and_missing_fields() -> None:
    http = fake_http(
        fixture("listing.html"), fixture("detail_25520.json"), fixture("detail_25319.json")
    )

    batch = DreamJobsSourceAdapter(http_request=http).fetch(company=CompanyStub())

    assert batch.requests_made == 3
    assert [record["source_job_id"] for record in batch.records] == ["25520", "25319"]
    assert batch.records[0] == {
        "source": "dreamjobs",
        "source_job_id": "25520",
        "source_job_url": "https://careers.example.test/jobs?activeOpportunityId=25520",
        "title": "Data & AI Lead",
        "location": "Praha, Česko",
        "country": "Česko",
        "city": "Praha",
        "workplace_type": "hybrid",
            "employment_type": "Full Time",
            "compensation_text": "65000-120000 CZK",
        "published_at": None,
        "description": "About the role\nBuild & improve data.\nLead delivery",
        "job_function": None,
        "seniority_level": None,
        "industry": None,
    }
    assert batch.records[1]["location"] is None
    assert batch.records[1]["description"] is None
    assert batch.records[1]["employment_type"] is None
    assert http.calls[0][:2] == ("GET", "https://careers.example.test/jobs")
    assert all(call[4] == DEFAULT_TIMEOUT_SECONDS for call in http.calls)
    assert all(call[1] == DREAMJOBS_API_URL for call in http.calls[1:])
    assert all(call[2]["jobnoone-webclientid"] == "example" for call in http.calls[1:])


def test_empty_verified_snapshot_is_complete_without_detail_requests() -> None:
    http = fake_http(fixture("listing_empty.html"))

    batch = DreamJobsSourceAdapter(http_request=http).fetch(company=CompanyStub())

    assert batch.records == ()
    assert batch.requests_made == 1


def test_graphql_pagination_collects_complete_deduplicated_snapshot() -> None:
    second_page = json.dumps(
        {
            "data": {
                "opportunities": {
                    "paginationInfo": {"itemsTotalCount": 2},
                    "opportunities": [
                        {
                            "id": "25319",
                            "title": "ML Engineer",
                            "company": {"id": "8", "name": "Example", "integrationType": "ATS"},
                            "locations": [],
                            "salary": None,
                            "workEnvironment": None,
                            "type": [],
                        }
                    ],
                }
            }
        }
    )
    http = fake_http(
        first_page_only(),
        second_page,
        fixture("detail_25520.json"),
        fixture("detail_25319.json"),
    )

    batch = DreamJobsSourceAdapter(http_request=http).fetch(company=CompanyStub())

    assert batch.requests_made == 4
    assert [record["source_job_id"] for record in batch.records] == ["25520", "25319"]
    page_payload = http.calls[1][3]
    assert page_payload is not None
    assert page_payload["variables"] == {
        "filter": {},
        "pagination": {"items": 1, "page": 2},
    }


def test_incomplete_or_repeated_pagination_fails_closed() -> None:
    repeated_page = json.dumps(
        {
            "data": {
                "opportunities": {
                    "paginationInfo": {"itemsTotalCount": 2},
                    "opportunities": [{"id": "25520", "title": "Repeated"}],
                }
            }
        }
    )

    with pytest.raises(SourceError, match="no new job IDs") as caught:
        DreamJobsSourceAdapter(http_request=fake_http(first_page_only(), repeated_page)).fetch(
            company=CompanyStub()
        )

    assert caught.value.requests_made == 2


def test_custom_domain_requires_multiple_dreamjobs_signals() -> None:
    not_dreamjobs = (
        '<html><body><script id="__NEXT_DATA__">'
        '{"page":"/jobs","props":{"pageProps":{}}}'
        "</script></body></html>"
    )

    with pytest.raises(SourceError, match="tenant identity|technical signals") as caught:
        DreamJobsSourceAdapter(http_request=fake_http(not_dreamjobs)).fetch(company=CompanyStub())

    assert caught.value.requests_made == 1


def test_failed_detail_returns_no_partial_batch_and_counts_attempt() -> None:
    http = fake_http(
        fixture("listing.html"), fixture("detail_25520.json"), TimeoutError("offline timeout")
    )

    with pytest.raises(SourceError, match="detail request failed") as caught:
        DreamJobsSourceAdapter(http_request=http).fetch(company=CompanyStub())

    assert caught.value.requests_made == 3


def test_request_limit_fails_before_incomplete_snapshot_can_return() -> None:
    http = fake_http(fixture("listing.html"), fixture("detail_25520.json"))

    with pytest.raises(SourceError, match="request safety limit") as caught:
        DreamJobsSourceAdapter(http_request=http, max_requests=2).fetch(company=CompanyStub())

    assert caught.value.requests_made == 2


def test_graphql_errors_fail_closed() -> None:
    http = fake_http(fixture("listing.html"), '{"errors":[{"message":"blocked"}]}')

    with pytest.raises(SourceError, match="GraphQL errors") as caught:
        DreamJobsSourceAdapter(http_request=http).fetch(company=CompanyStub())

    assert caught.value.requests_made == 2


@pytest.mark.parametrize("value", [0, -1, True, "20"])
def test_invalid_limits_and_timeout_are_rejected(value: object) -> None:
    with pytest.raises(ValueError):
        DreamJobsSourceAdapter(timeout_seconds=value)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        DreamJobsSourceAdapter(max_pages=value)  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        DreamJobsSourceAdapter(max_requests=value)  # type: ignore[arg-type]
