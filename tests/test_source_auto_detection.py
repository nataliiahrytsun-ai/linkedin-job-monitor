from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

import pytest

from discovery.network import CrawledPage, canonicalize_url


@pytest.fixture(scope="module", autouse=True)
def configured_django(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    apps = importlib.import_module("django.apps").apps
    if not apps.ready:
        database_path = tmp_path_factory.mktemp("source-detection-db") / "detection.sqlite3"
        os.environ["DJANGO_SETTINGS_MODULE"] = "job_monitor.settings"
        os.environ["JOB_MONITOR_SQLITE_PATH"] = str(database_path)
        importlib.import_module("django").setup()
    yield


def detection_module() -> Any:
    return importlib.import_module("companies.source_detection")


def detect_company_source_url(*args: object, **kwargs: object) -> Any:
    return detection_module().detect_company_source_url(*args, **kwargs)


def public_url(url: str) -> tuple[str, frozenset[str]]:
    return canonicalize_url(url), frozenset({"93.184.216.34"})


@dataclass
class StaticCrawler:
    page: CrawledPage
    calls: int = 0

    def crawl(self, seeds: tuple[str, ...]) -> tuple[CrawledPage, ...]:
        assert seeds == (self.page.requested_url,)
        self.calls += 1
        return (self.page,)


class NeverCrawler:
    def crawl(self, seeds: tuple[str, ...]) -> tuple[CrawledPage, ...]:
        raise AssertionError(f"host-based detection unexpectedly fetched {seeds!r}")


class FailingCrawler:
    def crawl(self, seeds: tuple[str, ...]) -> tuple[CrawledPage, ...]:
        raise TimeoutError(f"timed out fetching {seeds!r}")


@pytest.mark.parametrize(
    ("url", "expected_source", "expected_url"),
    [
        ("https://jobs.lever.co/acme/jobs/123", "lever", "https://jobs.lever.co/acme"),
        (
            "https://acme.applytojob.com/apply/jobs/",
            "jazzhr",
            "https://acme.applytojob.com/apply",
        ),
        (
            "https://acme.darwinbox.com/ms/candidate/careers",
            "darwinbox",
            "https://acme.darwinbox.com/ms/candidate/careers",
        ),
        (
            "https://acme.zohorecruit.com/jobs/Careers/123",
            "zoho_recruit",
            "https://acme.zohorecruit.com/jobs/Careers",
        ),
    ],
)
def test_hosted_supported_sources_are_detected_without_fetch(
    url: str,
    expected_source: str,
    expected_url: str,
) -> None:
    detected = detect_company_source_url(
        url,
        crawler=NeverCrawler(),
        public_url_validator=public_url,
    )

    assert (detected.source, detected.source_jobs_url) == (
        expected_source,
        expected_url,
    )


@pytest.mark.parametrize(
    ("url", "body", "expected_source"),
    [
        (
            "https://jobs.bgts.example/jobs/Careers",
            (
                '<input id="jobs"><input id="meta">'
                '<div id="career-website-main"></div>'
                '<script src="https://static.zohocdn.com/recruit/app.js"></script>'
            ),
            "zoho_recruit",
        ),
        (
            "https://careers.datasentics.example/jobs",
            '<script id="__NEXT_DATA__">api.dream.jobs</script>',
            "dreamjobs",
        ),
    ],
)
def test_custom_domain_ats_uses_bounded_page_signatures(
    url: str,
    body: str,
    expected_source: str,
) -> None:
    canonical = canonicalize_url(url)
    crawler = StaticCrawler(CrawledPage(canonical, canonical, body, (), 0))

    detected = detect_company_source_url(
        url,
        crawler=crawler,
        public_url_validator=public_url,
    )

    assert detected.source == expected_source
    assert detected.source_jobs_url == canonical
    assert crawler.calls == 1


def test_eligible_public_careers_page_becomes_generic() -> None:
    url = "https://www.example.com/careers/"
    body = (
        '<main><h1>Open jobs</h1><article class="job-card">'
        '<a href="/careers/senior-data-engineer">Senior Data Engineer</a>'
        "</article></main>"
    )
    crawler = StaticCrawler(CrawledPage(url, url, body, (), 0))

    detected = detect_company_source_url(
        url,
        crawler=crawler,
        public_url_validator=public_url,
    )

    assert detected.source == "generic"
    assert detected.source_jobs_url == url


def test_unknown_page_without_generic_eligibility_fails_closed() -> None:
    url = "https://www.example.com/about"
    crawler = StaticCrawler(
        CrawledPage(url, url, "<main><h1>About our company</h1></main>", (), 0)
    )

    with pytest.raises(
        detection_module().SourceAutoDetectionError,
        match="No supported job platform",
    ):
        detect_company_source_url(
            url,
            crawler=crawler,
            public_url_validator=public_url,
        )


def test_fetch_failure_is_reported_as_safe_detection_error() -> None:
    with pytest.raises(
        detection_module().SourceAutoDetectionError,
        match="could not be fetched safely",
    ):
        detect_company_source_url(
            "https://www.example.com/careers",
            crawler=FailingCrawler(),
            public_url_validator=public_url,
        )


@pytest.mark.parametrize("url", ["http://127.0.0.1/jobs", "http://localhost/jobs"])
def test_private_or_local_url_is_rejected(url: str) -> None:
    with pytest.raises(
        detection_module().SourceAutoDetectionError,
        match="Private, local, or unsafe",
    ):
        detect_company_source_url(url)


def test_internal_fixture_is_never_selected() -> None:
    url = "https://jobs.example.test/internal"
    crawler = StaticCrawler(
        CrawledPage(url, url, '<a href="/internal">Internal fixture</a>', (), 0)
    )

    with pytest.raises(detection_module().SourceAutoDetectionError):
        detect_company_source_url(
            url,
            crawler=crawler,
            public_url_validator=public_url,
        )


def test_external_linkedin_jobs_page_is_not_selected_as_generic() -> None:
    url = "https://www.linkedin.com/jobs/example-jobs"
    crawler = StaticCrawler(
        CrawledPage(
            url,
            url,
            '<a href="/jobs/view/123">Senior Data Engineer</a>',
            (),
            0,
        )
    )

    with pytest.raises(detection_module().SourceAutoDetectionError):
        detect_company_source_url(
            url,
            crawler=crawler,
            public_url_validator=public_url,
        )
