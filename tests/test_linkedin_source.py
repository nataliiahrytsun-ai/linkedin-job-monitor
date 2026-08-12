from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from scraping.sources.base import SourceError
from scraping.sources.linkedin import (
    LinkedInListingRecord,
    LinkedInSourceAdapter,
    linkedin_continuation_url,
    linkedin_job_id,
    linkedin_source_from_url,
    parse_linkedin_listing,
)

FIXTURES = Path(__file__).parent / "fixtures" / "linkedin"
SOURCE_URL = (
    "https://www.linkedin.com/jobs/acuity-analytics-jobs-worldwide"
    "?f_C=16691%2C30242966"
)


@dataclass
class CompanyStub:
    source_jobs_url: str | None = SOURCE_URL
    source: str = "linkedin"


@dataclass
class FakePageGet:
    responses: Iterator[str | bytes | Exception]
    calls: list[str] = field(default_factory=list)

    def __call__(self, url: str) -> str | bytes:
        self.calls.append(url)
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        return response


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def pages(*responses: str | bytes | Exception) -> FakePageGet:
    return FakePageGet(iter(responses))


def test_url_canonicalization_keeps_slug_and_sorted_numeric_company_ids() -> None:
    identity = linkedin_source_from_url(
        "https://in.linkedin.com/jobs/acuity-analytics-jobs-worldwide/"
        "?trk=discard&f_C=30242966%2C0016691&currentJobId=4447661197#discard"
    )

    assert identity.slug == "acuity-analytics-jobs-worldwide"
    assert identity.company_ids == ("16691", "30242966")
    assert identity.canonical_url == SOURCE_URL


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "http://www.linkedin.com/jobs/acuity-analytics-jobs-worldwide?f_C=16691",
        "https://example.com/jobs/acuity-analytics-jobs-worldwide?f_C=16691",
        "https://user@www.linkedin.com/jobs/acuity-analytics-jobs-worldwide?f_C=16691",
        "https://www.linkedin.com/jobs/search?f_C=16691",
        "https://www.linkedin.com/jobs/acuity-analytics-jobs-worldwide",
        "https://www.linkedin.com/jobs/acuity-analytics-jobs-worldwide?f_C=abc",
        "https://www.linkedin.com/jobs/acuity-analytics-jobs-worldwide?f_C=16691&start=25",
    ],
)
def test_url_validation_fails_closed(url: str | None) -> None:
    with pytest.raises(SourceError) as caught:
        linkedin_source_from_url(url)
    assert caught.value.requests_made == 0


def test_continuation_url_uses_confirmed_shape_and_sorted_identity() -> None:
    identity = linkedin_source_from_url(SOURCE_URL)

    url = linkedin_continuation_url(identity, 25)

    parsed = urlsplit(url)
    assert parsed.path == (
        "/jobs-guest/jobs/api/seeMoreJobPostings/acuity-analytics-jobs-worldwide"
    )
    assert parse_qs(parsed.query) == {
        "f_C": ["16691,30242966"],
        "start": ["25"],
    }


@pytest.mark.parametrize("start", [-1, True, 1.5, "25"])
def test_continuation_rejects_invalid_offset(start: object) -> None:
    with pytest.raises(ValueError):
        linkedin_continuation_url(
            linkedin_source_from_url(SOURCE_URL), start  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("urn:li:jobPosting:4445886946", "4445886946"),
        ("https://in.linkedin.com/jobs/view/title-4447661197?x=1", "4447661197"),
        ("https://www.linkedin.com/jobs/view/4439820960", "4439820960"),
        ("https://www.linkedin.com/jobs?currentJobId=4434981246", "4434981246"),
        ("4434981247", "4434981247"),
        ("not-an-id", None),
    ],
)
def test_stable_job_id_fallbacks(value: str, expected: str | None) -> None:
    assert linkedin_job_id(value) == expected


def test_real_shaped_listing_fields_and_canonical_job_urls() -> None:
    records = parse_linkedin_listing(
        fixture("source_listing_real_shaped.html"), page_url=SOURCE_URL
    )

    assert records == (
        LinkedInListingRecord(
            source_job_id="4445886946",
            title="Equity Quant Researcher",
            company="Acuity Analytics",
            location="Gurugram, Haryana, India",
            published_at="2026-08-03",
            source_job_url="https://www.linkedin.com/jobs/view/4445886946",
        ),
        LinkedInListingRecord(
            source_job_id="4439820960",
            title="Associate - Private Markets",
            company="Acuity Analytics",
            location="Bengaluru, Karnataka, India",
            published_at="2026-07-14",
            source_job_url="https://www.linkedin.com/jobs/view/4439820960",
        ),
    )


def test_sign_in_header_does_not_hide_valid_public_job_cards() -> None:
    html = fixture("source_listing_real_shaped.html").replace(
        "<div>", "<div><header>Sign in to LinkedIn</header>", 1
    )

    records = parse_linkedin_listing(html, page_url=SOURCE_URL)

    assert len(records) == 2


def test_hidden_challenge_text_does_not_create_a_false_positive() -> None:
    html = fixture("source_empty.html").replace(
        "</div>", '<span hidden>Verify you are human</span></div>'
    )

    assert parse_linkedin_listing(html, page_url=SOURCE_URL) == ()


def test_empty_fragment_is_the_only_successful_completion_signal() -> None:
    page_get = pages(
        fixture("source_listing_real_shaped.html"),
        fixture("source_continuation_new.html"),
        fixture("source_empty.html"),
    )

    batch = LinkedInSourceAdapter(page_get=page_get).fetch(company=CompanyStub())

    assert [record["source_job_id"] for record in batch.records] == [
        "4445886946",
        "4439820960",
        "4447661197",
    ]
    assert batch.records[0]["title"] == "Equity Quant Researcher"
    assert batch.records[0]["location"] == "Gurugram, Haryana, India"
    assert batch.records[0]["published_at"] == "2026-08-03"
    assert batch.records[0]["description"] is None
    assert batch.requests_made == 3
    assert page_get.calls == [
        SOURCE_URL,
        linkedin_continuation_url(linkedin_source_from_url(SOURCE_URL), 25),
        linkedin_continuation_url(linkedin_source_from_url(SOURCE_URL), 50),
    ]


def test_global_deduplication_preserves_first_seen_record() -> None:
    page_get = pages(
        fixture("source_listing_real_shaped.html"),
        fixture("source_continuation_overlap.html"),
        fixture("source_empty.html"),
    )

    batch = LinkedInSourceAdapter(page_get=page_get).fetch(company=CompanyStub())

    assert [record["source_job_id"] for record in batch.records] == [
        "4445886946",
        "4439820960",
    ]
    assert batch.records[1]["location"] == "Bengaluru, Karnataka, India"


def test_repeated_content_fails_instead_of_returning_partial_snapshot() -> None:
    listing = fixture("source_listing_real_shaped.html")
    page_get = pages(listing, listing)

    with pytest.raises(SourceError, match="repeated content") as caught:
        LinkedInSourceAdapter(page_get=page_get).fetch(company=CompanyStub())

    assert caught.value.requests_made == 2


def test_overlap_exhaustion_fails_before_empty_completion() -> None:
    overlap = fixture("source_continuation_overlap.html")
    page_get = pages(
        fixture("source_listing_real_shaped.html"),
        overlap,
        overlap.replace("<div>", '<div data-page="two">', 1),
    )

    with pytest.raises(SourceError, match="overlap allowance") as caught:
        LinkedInSourceAdapter(page_get=page_get, overlap_limit=1).fetch(
            company=CompanyStub()
        )

    assert caught.value.requests_made == 3


def test_hard_limit_fails_before_empty_completion() -> None:
    page_get = pages(
        fixture("source_listing_real_shaped.html"),
        fixture("source_continuation_new.html"),
    )

    with pytest.raises(SourceError, match="limit reached") as caught:
        LinkedInSourceAdapter(
            page_get=page_get,
            max_pages=2,
            max_requests=2,
        ).fetch(company=CompanyStub())

    assert caught.value.requests_made == 2


def test_repeated_url_fails_before_request(monkeypatch: pytest.MonkeyPatch) -> None:
    page_get = pages(fixture("source_listing_real_shaped.html"))
    monkeypatch.setattr(
        "scraping.sources.linkedin.linkedin_continuation_url",
        lambda identity, start: identity.canonical_url,
    )

    with pytest.raises(SourceError, match="repeated a URL") as caught:
        LinkedInSourceAdapter(page_get=page_get).fetch(company=CompanyStub())

    assert caught.value.requests_made == 1
    assert len(page_get.calls) == 1


@pytest.mark.parametrize("name", ["source_challenge.html", "source_login.html"])
def test_challenge_and_login_pages_fail_closed(name: str) -> None:
    page_get = pages(fixture(name))

    with pytest.raises(SourceError, match="challenge, login, or access-denied") as caught:
        LinkedInSourceAdapter(page_get=page_get).fetch(company=CompanyStub())

    assert caught.value.requests_made == 1


def test_access_denied_and_authwall_url_fail_closed() -> None:
    with pytest.raises(SourceError, match="challenge, login, or access-denied"):
        parse_linkedin_listing(
            "<html><body>Access denied</body></html>",
            page_url="https://www.linkedin.com/authwall",
            requests_made=1,
        )


def test_malformed_card_and_loose_link_fail_closed() -> None:
    with pytest.raises(SourceError, match="malformed job card"):
        parse_linkedin_listing(
            fixture("source_malformed.html"), page_url=SOURCE_URL, requests_made=1
        )


def test_arbitrary_empty_html_is_not_proof_of_snapshot_completion() -> None:
    page_get = pages("<html><body></body></html>")

    with pytest.raises(SourceError, match="malformed or not a listing page") as caught:
        LinkedInSourceAdapter(page_get=page_get).fetch(company=CompanyStub())

    assert caught.value.requests_made == 1
    with pytest.raises(SourceError, match="unstructured job links"):
        parse_linkedin_listing(
            '<a href="https://www.linkedin.com/jobs/view/title-4447661197">Job</a>',
            page_url=SOURCE_URL,
            requests_made=1,
        )


def test_no_transport_means_production_network_execution_is_disabled() -> None:
    with pytest.raises(SourceError, match="production network execution is disabled") as caught:
        LinkedInSourceAdapter().fetch(company=CompanyStub())

    assert caught.value.requests_made == 0


def test_transport_failure_is_counted_once_without_retry() -> None:
    page_get = pages(TimeoutError("offline synthetic timeout"))

    with pytest.raises(SourceError, match="offline page transport failed") as caught:
        LinkedInSourceAdapter(page_get=page_get).fetch(company=CompanyStub())

    assert caught.value.requests_made == 1
    assert len(page_get.calls) == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"continuation_start": -1},
        {"continuation_start": True},
        {"continuation_step": 0},
        {"max_pages": 0},
        {"max_requests": 0},
        {"overlap_limit": 0},
    ],
)
def test_invalid_limits_are_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        LinkedInSourceAdapter(page_get=pages(), **kwargs)  # type: ignore[arg-type]
