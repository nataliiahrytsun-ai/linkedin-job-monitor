from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest
from lxml import html as lxml_html  # type: ignore[import-untyped]

from scraping.sources.base import SourceError
from scraping.sources.zoho_recruit import (
    MAX_PUBLIC_CAREER_SITE_JOBS,
    ZohoRecruitSourceAdapter,
    zoho_recruit_source_from_url,
)

FIXTURES = Path(__file__).parent / "fixtures" / "zoho_recruit"


@dataclass
class CompanyStub:
    source_jobs_url: str | None = "https://jobs.example.com/jobs/Careers"
    source: str = "zoho_recruit"


def fixture() -> str:
    return (FIXTURES / "listing.html").read_text(encoding="utf-8")


def replace_payload(input_id: str, value: object) -> str:
    document = lxml_html.fromstring(fixture())
    nodes = document.xpath(f'//input[@id="{input_id}"]')
    assert len(nodes) == 1
    nodes[0].set("value", json.dumps(value))
    return cast(str, lxml_html.tostring(document, encoding="unicode"))


@pytest.mark.parametrize(
    "source_url",
    [
        "https://jobs.example.com/jobs/Careers",
        "https://jobs.example.com/jobs/Careers/",
        "https://jobs.example.com/jobs/Careers/41800000007964561/Data-Engineer?source=CareerSite",
        "https://tenant.zohorecruit.com/jobs/careers#openings",
    ],
)
def test_source_urls_canonicalize_to_public_listing(source_url: str) -> None:
    source = zoho_recruit_source_from_url(source_url)

    expected_page = "careers" if "zohorecruit.com" in source_url else "Careers"
    expected_host = (
        "tenant.zohorecruit.com"
        if "zohorecruit.com" in source_url
        else "jobs.example.com"
    )
    assert source.listing_url == f"https://{expected_host}/jobs/{expected_page}"


@pytest.mark.parametrize(
    "source_url",
    [
        None,
        "",
        "http://jobs.example.com/jobs/Careers",
        "https://jobs.example.com/Careers",
        "https://jobs.example.com/jobs/",
        "https://user@jobs.example.com/jobs/Careers",
        "https://jobs.example.com:443/jobs/Careers",
    ],
)
def test_invalid_source_urls_fail_closed(source_url: str | None) -> None:
    with pytest.raises(SourceError):
        zoho_recruit_source_from_url(source_url)


def test_adapter_maps_complete_embedded_snapshot() -> None:
    calls: list[tuple[str, float]] = []

    def http_get(url: str, timeout_seconds: float) -> str:
        calls.append((url, timeout_seconds))
        return fixture()

    batch = ZohoRecruitSourceAdapter(http_get=http_get).fetch(company=CompanyStub())

    assert len(calls) == 1
    assert batch.requests_made == 1
    assert batch.records == (
        {
            "source": "zoho_recruit",
            "source_job_id": "41800000007964561",
            "source_job_url": (
                "https://jobs.example.com/jobs/Careers/41800000007964561"
            ),
            "title": "Data Engineer",
            "location": "London, Greater London, United Kingdom",
            "country": "United Kingdom",
            "city": "London",
            "workplace_type": "Remote",
                "employment_type": "Full time",
                "compensation_text": None,
            "published_at": "2026-02-19",
            "description": "Build & operate data platforms.",
            "job_function": None,
            "seniority_level": None,
            "industry": "IT Services",
        },
        {
            "source": "zoho_recruit",
            "source_job_id": "41800000009228516",
            "source_job_url": (
                "https://jobs.example.com/jobs/Careers/41800000009228516"
            ),
            "title": "AI Platform Engineer",
            "location": "Warsaw, Mazowieckie, Poland",
            "country": "Poland",
            "city": "Warsaw",
            "workplace_type": None,
                "employment_type": "Contract",
                "compensation_text": None,
            "published_at": "2026-07-10",
            "description": "Build shared AI platform services.",
            "job_function": None,
            "seniority_level": None,
            "industry": "Technology",
        },
    )


def test_empty_jobs_payload_fails_closed() -> None:
    adapter = ZohoRecruitSourceAdapter(http_get=lambda _url, _timeout: replace_payload("jobs", []))

    with pytest.raises(SourceError, match="no verifiable jobs") as caught:
        adapter.fetch(company=CompanyStub())

    assert caught.value.requests_made == 1


@pytest.mark.parametrize("input_id", ["jobs", "meta", "pageJson", "moduleMeta"])
def test_malformed_embedded_payload_fails_closed(input_id: str) -> None:
    document = lxml_html.fromstring(fixture())
    node = document.xpath(f'//input[@id="{input_id}"]')[0]
    node.set("value", "{not-json")
    body = lxml_html.tostring(document, encoding="unicode")

    with pytest.raises(SourceError, match="malformed JSON"):
        ZohoRecruitSourceAdapter(http_get=lambda _url, _timeout: body).fetch(
            company=CompanyStub()
        )


def test_visible_pagination_fails_closed() -> None:
    body = fixture().replace(
        '<div id="career-website-main"',
        '<a rel="next" href="?page=2">Next</a><div id="career-website-main"',
    )

    with pytest.raises(SourceError, match="pagination"):
        ZohoRecruitSourceAdapter(http_get=lambda _url, _timeout: body).fetch(
            company=CompanyStub()
        )


def test_public_career_site_limit_fails_closed_as_incomplete() -> None:
    template_job = {
        "Posting_Title": "Platform Engineer",
        "Job_Opening_Name": "Platform Engineer",
        "Publish": True,
    }
    jobs = [
        {**template_job, "id": str(41800000010000000 + index)}
        for index in range(MAX_PUBLIC_CAREER_SITE_JOBS)
    ]

    with pytest.raises(SourceError, match="completeness is unknown"):
        ZohoRecruitSourceAdapter(
            http_get=lambda _url, _timeout: replace_payload("jobs", jobs)
        ).fetch(company=CompanyStub())


def test_missing_platform_signature_fails_closed() -> None:
    body = fixture().replace("static.zohocdn.com/recruit/", "cdn.example.com/assets/")

    with pytest.raises(SourceError, match="platform asset signature"):
        ZohoRecruitSourceAdapter(http_get=lambda _url, _timeout: body).fetch(
            company=CompanyStub()
        )


def test_logged_in_portal_metadata_fails_closed() -> None:
    document = lxml_html.fromstring(fixture())
    meta_node = document.xpath('//input[@id="meta"]')[0]
    meta = json.loads(meta_node.get("value"))
    meta["portal_user"]["is_loggedin"] = True
    meta_node.set("value", json.dumps(meta))
    body = lxml_html.tostring(document, encoding="unicode")

    with pytest.raises(SourceError, match="unauthenticated public snapshot"):
        ZohoRecruitSourceAdapter(http_get=lambda _url, _timeout: body).fetch(
            company=CompanyStub()
        )


def test_duplicate_stable_id_fails_closed() -> None:
    document = lxml_html.fromstring(fixture())
    jobs_node = document.xpath('//input[@id="jobs"]')[0]
    jobs = json.loads(jobs_node.get("value"))
    jobs.append(dict(jobs[0]))
    jobs_node.set("value", json.dumps(jobs))
    body = lxml_html.tostring(document, encoding="unicode")

    with pytest.raises(SourceError, match="duplicate stable ID"):
        ZohoRecruitSourceAdapter(http_get=lambda _url, _timeout: body).fetch(
            company=CompanyStub()
        )
