from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from scraping.sources.base import SourceBatch, SourceError
from scraping.sources.jazzhr import (
    DEFAULT_TIMEOUT_SECONDS,
    JazzHRSourceAdapter,
    jazzhr_job_id_from_url,
    jazzhr_source_from_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "jazzhr"


@dataclass
class CompanyStub:
    source_jobs_url: str | None = "https://example.applytojob.com/apply"
    source: str = "jazzhr"


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


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def fake_http(*responses: str | bytes | Exception) -> FakeHttpGet:
    return FakeHttpGet(iter(responses))


def complete_http() -> FakeHttpGet:
    return fake_http(
        fixture("listing.html"),
        fixture("detail_data_lead.html"),
        fixture("detail_platform.html"),
    )


def single_job_listing() -> str:
    return """
        <div class="jobs-list"><ul class="list-group"><li>
          <a href="/apply/aOo71i87lE/Manager-PMO-Product-Management-MFD">
            Manager PMO &amp; Product Management (M/F/D)
          </a>
        </li></ul></div>
    """


def fetch_complete() -> tuple[FakeHttpGet, SourceBatch]:
    http_get = complete_http()
    batch = JazzHRSourceAdapter(http_get=http_get).fetch(company=CompanyStub())
    return http_get, batch


@pytest.mark.parametrize(
    "source_url",
    [
        "https://example.applytojob.com/apply",
        "https://example.applytojob.com/apply/",
        "https://example.applytojob.com/apply/jobs",
        "https://example.applytojob.com/apply/jobs/?search=data#openings",
    ],
)
def test_valid_source_urls_canonicalize_to_public_listing(source_url: str) -> None:
    location = jazzhr_source_from_url(source_url)

    assert location.host == "example.applytojob.com"
    assert location.listing_url == "https://example.applytojob.com/apply"


@pytest.mark.parametrize(
    "source_url",
    [
        None,
        "",
        "http://example.applytojob.com/apply",
        "https://applytojob.com/apply",
        "https://nested.example.applytojob.com/apply",
        "https://example.com/apply",
        "https://example.applytojob.com/other",
        "https://user@example.applytojob.com/apply",
        "https://example.applytojob.com:443/apply",
    ],
)
def test_invalid_source_urls_fail_closed(source_url: str | None) -> None:
    with pytest.raises(SourceError):
        jazzhr_source_from_url(source_url)


def test_current_and_legacy_detail_urls_share_the_opaque_stable_id() -> None:
    current = "https://example.applytojob.com/apply/AbC123xyZ9/Data-Analytics-Lead"
    legacy = "https://example.applytojob.com/apply/jobs/details/AbC123xyZ9?&"

    assert jazzhr_job_id_from_url(current) == "AbC123xyZ9"
    assert jazzhr_job_id_from_url(legacy) == "AbC123xyZ9"


@pytest.mark.parametrize(
    "job_url",
    [
        "https://other.example.test/apply/AbC123xyZ9/title",
        "https://example.applytojob.com/apply/jobs",
        "https://example.applytojob.com/apply/short/title",
        "https://example.applytojob.com/apply/jobs/details/",
    ],
)
def test_invalid_or_unidentified_detail_url_is_rejected(job_url: str) -> None:
    with pytest.raises(SourceError):
        jazzhr_job_id_from_url(job_url)


def test_complete_snapshot_maps_listing_and_detail_fields() -> None:
    http_get, batch = fetch_complete()

    assert batch.requests_made == 3
    assert len(http_get.calls) == 3
    assert http_get.calls[0] == (
        "https://example.applytojob.com/apply",
        DEFAULT_TIMEOUT_SECONDS,
    )
    assert batch.records[0] == {
        "source": "jazzhr",
        "source_job_id": "AbC123xyZ9",
        "source_job_url": (
            "https://example.applytojob.com/apply/AbC123xyZ9/Data-Analytics-Lead"
        ),
        "title": "Data & Analytics Lead",
        "location": "Vienna, Austria",
        "country": "Austria",
        "city": None,
        "workplace_type": None,
            "employment_type": "Full Time",
            "compensation_text": None,
        "published_at": "2026-08-01",
        "description": "Build & improve data.\nWork across teams.",
        "job_function": "Data & AI",
        "seniority_level": None,
        "industry": "Technology",
    }
    assert batch.records[1]["source_job_url"] == (
        "https://example.applytojob.com/apply/jobs/details/QwE987rtY6"
    )
    assert batch.records[1]["location"] is None
    assert batch.records[1]["employment_type"] is None
    assert batch.records[1]["published_at"] is None


def test_duplicate_visible_refs_do_not_collapse_distinct_opaque_ids() -> None:
    _, batch = fetch_complete()

    assert [record["source_job_id"] for record in batch.records] == [
        "AbC123xyZ9",
        "QwE987rtY6",
    ]


def test_repeated_snapshot_produces_stable_records() -> None:
    first = JazzHRSourceAdapter(http_get=complete_http()).fetch(company=CompanyStub())
    second = JazzHRSourceAdapter(http_get=complete_http()).fetch(company=CompanyStub())

    assert first.records == second.records


def test_equivalent_duplicate_stable_id_is_deduplicated_safely() -> None:
    listing = fixture("listing.html").replace(
        "</ul>\n    </div>",
        '<li><a href="/apply/jobs/details/AbC123xyZ9">Data &amp; Analytics Lead</a></li>'
        "</ul>\n    </div>",
    )
    http_get = fake_http(
        listing,
        fixture("detail_data_lead.html"),
        fixture("detail_platform.html"),
    )

    batch = JazzHRSourceAdapter(http_get=http_get).fetch(company=CompanyStub())

    assert len(batch.records) == 2
    assert batch.requests_made == 3


def test_conflicting_duplicate_stable_id_fails_before_detail_requests() -> None:
    listing = fixture("listing.html").replace(
        "</ul>\n    </div>",
        '<li><a href="/apply/jobs/details/AbC123xyZ9">Different Job</a></li>'
        "</ul>\n    </div>",
    )
    http_get = fake_http(listing)

    with pytest.raises(SourceError, match="conflicting jobs") as caught:
        JazzHRSourceAdapter(http_get=http_get).fetch(company=CompanyStub())

    assert caught.value.requests_made == 1
    assert len(http_get.calls) == 1


@pytest.mark.parametrize(
    ("listing", "message"),
    [
        ("<html><title>Career Page</title><body>Loading</body></html>", "malformed"),
        (
            '<div class="jobs-list"><a href="/apply/x/title">Job</a></div>',
            "malformed",
        ),
        (
            '<div class="jobs-list"><a href="/apply/AbC123xyZ9/x">Job</a>'
            '<a rel="next" href="?page=2">Next</a></div>',
            "pagination",
        ),
    ],
)
def test_malformed_or_incomplete_listing_fails_closed(
    listing: str,
    message: str,
) -> None:
    http_get = fake_http(listing)

    with pytest.raises(SourceError, match=message) as caught:
        JazzHRSourceAdapter(http_get=http_get).fetch(company=CompanyStub())

    assert caught.value.requests_made == 1


def test_explicit_empty_listing_is_a_complete_one_request_snapshot() -> None:
    listing = '<div class="jobs-list">No jobs are currently available.</div>'

    batch = JazzHRSourceAdapter(http_get=fake_http(listing)).fetch(company=CompanyStub())

    assert batch.records == ()
    assert batch.requests_made == 1


def test_failed_required_detail_counts_attempt_and_returns_no_partial_batch() -> None:
    http_get = fake_http(
        fixture("listing.html"),
        fixture("detail_data_lead.html"),
        TimeoutError("offline fake timeout"),
    )

    with pytest.raises(SourceError, match="detail request failed") as caught:
        JazzHRSourceAdapter(http_get=http_get).fetch(company=CompanyStub())

    assert caught.value.requests_made == 3


@pytest.mark.parametrize(
    "body",
    [
        "<html><title>Access Denied</title><h1>Access denied</h1></html>",
        '<html><body><div id="captcha-container"></div></body></html>',
        '<html><body><form action="/login"></form></body></html>',
    ],
)
def test_access_restriction_or_challenge_page_fails_closed(body: str) -> None:
    with pytest.raises(SourceError, match="restriction or challenge") as caught:
        JazzHRSourceAdapter(http_get=fake_http(body)).fetch(company=CompanyStub())

    assert caught.value.requests_made == 1


def test_application_form_captcha_does_not_hide_valid_public_job_content() -> None:
    _, batch = fetch_complete()

    assert batch.records[0]["source_job_id"] == "AbC123xyZ9"


def test_mixed_jsonld_containers_select_the_job_matching_the_detail_url() -> None:
    http_get = fake_http(single_job_listing(), fixture("detail_mixed_jsonld.html"))

    batch = JazzHRSourceAdapter(http_get=http_get).fetch(company=CompanyStub())

    assert batch.requests_made == 2
    assert batch.records[0]["source_job_id"] == "aOo71i87lE"
    assert batch.records[0]["title"] == "Manager PMO & Product Management (M/F/D)"
    assert batch.records[0]["location"] == "Vienna, Austria"
    assert batch.records[0]["description"] == "Lead product planning & delivery."


def test_organization_only_jsonld_uses_strict_html_job_fallback() -> None:
    http_get = fake_http(
        single_job_listing(),
        fixture("detail_organization_only.html"),
    )

    batch = JazzHRSourceAdapter(http_get=http_get).fetch(company=CompanyStub())

    record = batch.records[0]
    assert batch.requests_made == 2
    assert record["source_job_id"] == "aOo71i87lE"
    assert record["source_job_url"] == (
        "https://example.applytojob.com/apply/"
        "aOo71i87lE/Manager-PMO-Product-Management-MFD"
    )
    assert record["title"] == "Manager PMO & Product Management (M/F/D)"
    assert record["location"] == "Darmstadt, Germany"
    assert record["employment_type"] == "Full Time"
    assert record["job_function"] == "Product"
    assert record["description"] == (
        "About the role\n"
        "Lead portfolio planning & delivery.\n"
        "Responsibilities\n"
        "Own product strategy.\n"
        "Coordinate delivery teams.\n"
        "Requirements\n"
        "Strong programme leadership.\n"
        "Clear stakeholder communication."
    )
    assert record["seniority_level"] == "Experienced"
    assert "First Name" not in str(record["description"])
    assert "Resume" not in str(record["description"])
    assert "captcha" not in str(record["description"]).casefold()
    assert "Submit Application" not in str(record["description"])


def test_html_fallback_repeated_parsing_keeps_opaque_identity_stable() -> None:
    first = JazzHRSourceAdapter(
        http_get=fake_http(single_job_listing(), fixture("detail_organization_only.html"))
    ).fetch(company=CompanyStub())
    second = JazzHRSourceAdapter(
        http_get=fake_http(single_job_listing(), fixture("detail_organization_only.html"))
    ).fetch(company=CompanyStub())

    assert first.records == second.records
    assert first.records[0]["source_job_id"] == "aOo71i87lE"


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ("<h2> Manager", "<h3> Manager", "job title"),
        (
            "<h2> Manager",
            "<h2>Duplicate title</h2><h2> Manager",
            "job title",
        ),
        ('id="job-description"', 'id="not-job-description"', "job description"),
        (
            '<div id="job-description" class="description">',
            '<div id="job-description" class="description"></div><div>',
            "full description",
        ),
        (
            'id="job-description" class="description"',
            'id="outside-description" class="description"',
            "job description",
        ),
        (
            "aOo71i87lE/Manager-PMO-Product-Management-MFD\">\n    <script",
            "Other98765/Other-Role\">\n    <script",
            "conflicting stable job ID",
        ),
    ],
)
def test_html_fallback_rejects_missing_ambiguous_or_conflicting_structure(
    old: str,
    new: str,
    message: str,
) -> None:
    detail = fixture("detail_organization_only.html").replace(old, new, 1)

    with pytest.raises(SourceError, match=message) as caught:
        JazzHRSourceAdapter(http_get=fake_http(single_job_listing(), detail)).fetch(
            company=CompanyStub()
        )

    assert caught.value.requests_made == 2


def test_html_fallback_rejects_description_inside_application_form() -> None:
    detail = fixture("detail_organization_only.html")
    detail = detail.replace('id="job-description"', 'id="original-description"')
    detail = detail.replace(
        '<form id="form_submit_new_resume" method="post">',
        '<form id="form_submit_new_resume" method="post">'
        '<div id="job-description"><p>Not valid job content.</p></div>',
    )

    with pytest.raises(SourceError, match="overlaps the application form") as caught:
        JazzHRSourceAdapter(http_get=fake_http(single_job_listing(), detail)).fetch(
            company=CompanyStub()
        )

    assert caught.value.requests_made == 2


def test_html_fallback_rejects_form_inside_description() -> None:
    detail = fixture("detail_organization_only.html").replace(
        '<h3>About the role</h3>',
        '<form id="embedded-application"><h3>About the role</h3></form>',
    )

    with pytest.raises(SourceError, match="overlaps the application form") as caught:
        JazzHRSourceAdapter(http_get=fake_http(single_job_listing(), detail)).fetch(
            company=CompanyStub()
        )

    assert caught.value.requests_made == 2


def test_unrelated_applytojob_page_cannot_use_html_fallback() -> None:
    unrelated = """
        <html><head><script type="application/ld+json">
          {"@type": "Organization", "name": "Example"}
        </script></head><body><h2>Company page</h2></body></html>
    """

    with pytest.raises(SourceError, match="job header") as caught:
        JazzHRSourceAdapter(
            http_get=fake_http(single_job_listing(), unrelated)
        ).fetch(company=CompanyStub())

    assert caught.value.requests_made == 2


def test_ambiguous_jsonld_is_not_masked_by_valid_html_fallback() -> None:
    conflicting = """
      <script type="application/ld+json">
        [
          {
            "@type": "JobPosting",
            "url": "https://example.applytojob.com/apply/aOo71i87lE/First",
            "title": "First"
          },
          {
            "@type": "JobPosting",
            "url": "https://example.applytojob.com/apply/aOo71i87lE/Second",
            "title": "Second"
          }
        ]
      </script>
    """
    detail = fixture("detail_organization_only.html").replace(
        "</head>", conflicting + "</head>"
    )

    with pytest.raises(SourceError, match="ambiguous JobPosting") as caught:
        JazzHRSourceAdapter(http_get=fake_http(single_job_listing(), detail)).fetch(
            company=CompanyStub()
        )

    assert caught.value.requests_made == 2


def test_multiple_jobpostings_matching_the_detail_url_remain_ambiguous() -> None:
    detail = fixture("detail_mixed_jsonld.html").replace(
        "Other98765/Other-Role",
        "aOo71i87lE/Conflicting-Role",
    )
    http_get = fake_http(single_job_listing(), detail)

    with pytest.raises(SourceError, match="ambiguous JobPosting") as caught:
        JazzHRSourceAdapter(http_get=http_get).fetch(company=CompanyStub())

    assert caught.value.requests_made == 2


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        (
            '"description": "<p>Build &amp; improve data.</p>'
            '<p>Work   across teams.</p>"',
            '"description": ""',
            "description",
        ),
        ('"title": "Data & Analytics Lead"', '"title": "Different title"', "titles conflict"),
        (
            "AbC123xyZ9/Data-Analytics-Lead?source=career",
            "Other12345/Different?source=career",
            "conflicting stable job ID",
        ),
        ('"@type": "JobPosting"', '"@type": "WebPage"', "job header"),
    ],
)
def test_malformed_required_detail_fails_closed(old: str, new: str, message: str) -> None:
    detail = fixture("detail_data_lead.html").replace(old, new)
    http_get = fake_http(fixture("listing.html"), detail)

    with pytest.raises(SourceError, match=message) as caught:
        JazzHRSourceAdapter(http_get=http_get).fetch(company=CompanyStub())

    assert caught.value.requests_made == 2


def test_injected_transport_receives_configured_timeout() -> None:
    http_get = complete_http()

    JazzHRSourceAdapter(http_get=http_get, timeout_seconds=7.5).fetch(
        company=CompanyStub()
    )

    assert all(timeout == 7.5 for _url, timeout in http_get.calls)


@pytest.mark.parametrize("timeout", [0, -1, True, "20"])
def test_invalid_timeout_is_rejected(timeout: object) -> None:
    with pytest.raises(ValueError):
        JazzHRSourceAdapter(
            http_get=fake_http(),
            timeout_seconds=timeout,  # type: ignore[arg-type]
        )
