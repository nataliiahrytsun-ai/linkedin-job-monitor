from __future__ import annotations

import importlib
import os
from collections.abc import Iterator
from threading import Event
from types import SimpleNamespace
from typing import Any, ClassVar, cast

import pytest

detectors_module = importlib.import_module("discovery.detectors")
network_module = importlib.import_module("discovery.network")
search_module = importlib.import_module("discovery.search")
detect_page: Any = detectors_module.detect_page
BoundedCrawler: Any = network_module.BoundedCrawler
CrawledPage: Any = network_module.CrawledPage
HttpResponse: Any = network_module.HttpResponse
UnsafeUrlError: Any = network_module.UnsafeUrlError
canonicalize_url: Any = network_module.canonicalize_url
SearchConfigurationError: Any = search_module.SearchConfigurationError
SearchResult: Any = search_module.SearchResult


def service_module() -> Any:
    return importlib.import_module("discovery.service")


@pytest.fixture(scope="module", autouse=True)
def migrated_database(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    apps = importlib.import_module("django.apps").apps
    if not apps.ready:
        database_path = tmp_path_factory.mktemp("discovery-db") / "discovery.sqlite3"
        os.environ["DJANGO_SETTINGS_MODULE"] = "job_monitor.settings"
        os.environ["JOB_MONITOR_SQLITE_PATH"] = str(database_path)
        importlib.import_module("django").setup()
    importlib.import_module("django.core.management").call_command(
        "migrate", interactive=False, verbosity=0
    )
    yield


@pytest.fixture(autouse=True)
def clean_database(migrated_database: None) -> None:
    importlib.import_module("django.core.management").call_command(
        "flush", interactive=False, verbosity=0
    )


def model(name: str) -> Any:
    return importlib.import_module("django.apps").apps.get_model(name)


def company(name: str = "Data Sentics") -> Any:
    return model("companies.Company").objects.create(name=name, source="")


class FakeSearch:
    def __init__(self, *results: Any, error: Exception | None = None) -> None:
        self.results = results
        self.error = error
        self.queries: list[str] = []

    def search(self, query: str, *, limit: int = 5) -> tuple[Any, ...]:
        self.queries.append(query)
        if self.error:
            raise self.error
        return self.results[:limit]


class FakeCrawler:
    def __init__(self, *pages: Any, errors: tuple[str, ...] = ()) -> None:
        self.pages = pages
        self.seeds: tuple[str, ...] = ()
        self.errors = errors

    def crawl(self, seeds: tuple[str, ...]) -> tuple[Any, ...]:
        self.seeds = seeds
        return self.pages


def page(url: str, body: str = "", links: tuple[str, ...] = ()) -> Any:
    return CrawledPage(url, url, body, links, 0)


@pytest.mark.parametrize(
    ("url", "body", "platform"),
    [
        ("https://jobs.lever.co/acme", "", "lever"),
        ("https://acme.applytojob.com/apply", "", "jazzhr"),
        ("https://acme.dream.jobs/jobs", "", "dreamjobs"),
        ("https://acme.darwinbox.com/ms/candidate/careers", "", "darwinbox"),
    ],
)
def test_supported_platform_detectors(url: str, body: str, platform: str) -> None:
    detection = detect_page(page(url, body))[0]
    assert detection.platform == platform
    assert detection.supported is True
    assert detection.confidence >= 90


def test_every_user_selectable_adapter_exposes_discovery_hints() -> None:
    registry = importlib.import_module("scraping.sources.registry")
    hints = detectors_module.registered_discovery_hints()

    assert {hint.platform for hint in hints} == set(registry.user_selectable_source_keys())
    assert all(hint.search_hints and hint.technical_signals for hint in hints)


def test_unsupported_platform_detector_preserves_evidence() -> None:
    detection = detect_page(page("https://boards.greenhouse.io/acme", "greenhouse.io"))[0]
    assert detection.platform == "greenhouse"
    assert detection.supported is False
    assert detection.evidence


def test_dreamjobs_vendor_link_is_not_a_tenant_source() -> None:
    custom_jobs = "https://careers.datasentics.com/jobs"
    detections = detect_page(
        page(
            custom_jobs,
            "__NEXT_DATA__ api.dream.jobs",
            ("https://dream.jobs/jobs", "https://business.dream.jobs/terms"),
        )
    )
    assert [(item.platform, item.canonical_url) for item in detections] == [
        ("dreamjobs", custom_jobs)
    ]


@pytest.mark.parametrize(
    "url",
    ["file:///etc/passwd", "ftp://example.com/jobs", "http://user:pass@example.com/jobs"],
)
def test_canonicalize_rejects_unsafe_url_shapes(url: str) -> None:
    with pytest.raises(UnsafeUrlError):
        canonicalize_url(url)


def test_canonicalize_normalizes_host_path_and_fragment() -> None:
    assert canonicalize_url("HTTPS://Example.COM/jobs#open") == "https://example.com/jobs"


class FakeTransport:
    def __init__(self, responses: dict[str, HttpResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, *, timeout_seconds: float) -> HttpResponse:
        self.calls.append(url)
        return self.responses[url]


def public_resolver(_host: str) -> frozenset[str]:
    return frozenset({"93.184.216.34"})


def test_crawler_revalidates_redirect_and_blocks_private_target() -> None:
    first = "https://example.com/"
    transport = FakeTransport(
        {first: HttpResponse(302, first, {"location": "http://127.0.0.1/admin"}, b"")}
    )

    def resolver(host: str) -> frozenset[str]:
        if host == "127.0.0.1":
            raise UnsafeUrlError("private")
        return public_resolver(host)

    assert BoundedCrawler(transport=transport, resolver=resolver).crawl((first,)) == ()
    assert transport.calls == [first]


def test_crawler_detects_dns_rebinding() -> None:
    url = "https://example.com/"
    calls = 0

    def resolver(_host: str) -> frozenset[str]:
        nonlocal calls
        calls += 1
        return frozenset({"93.184.216.34" if calls == 1 else "93.184.216.35"})

    transport = FakeTransport({url: HttpResponse(200, url, {"content-type": "text/html"}, b"ok")})
    assert BoundedCrawler(transport=transport, resolver=resolver).crawl((url,)) == ()


def test_crawler_enforces_body_and_content_type_limits() -> None:
    for headers, body in (
        ({"content-type": "image/png"}, b"x"),
        ({"content-type": "text/html"}, b"xxxx"),
    ):
        url = "https://example.com/"
        crawler = BoundedCrawler(
            transport=FakeTransport({url: HttpResponse(200, url, headers, body)}),
            resolver=public_resolver,
            max_body_bytes=3,
        )
        assert crawler.crawl((url,)) == ()


def test_crawler_request_limit_includes_redirects() -> None:
    first = "https://example.com/"
    second = "https://example.com/jobs"
    transport = FakeTransport(
        {
            first: HttpResponse(302, first, {"location": second}, b""),
            second: HttpResponse(200, second, {"content-type": "text/html"}, b"jobs"),
        }
    )
    assert (
        BoundedCrawler(transport=transport, resolver=public_resolver, max_requests=1).crawl(
            (first,)
        )
        == ()
    )
    assert transport.calls == [first]


def test_crawler_records_successful_redirects() -> None:
    first = "https://example.com/"
    second = "https://example.com/careers"
    transport = FakeTransport(
        {
            first: HttpResponse(302, first, {"location": second}, b""),
            second: HttpResponse(200, second, {"content-type": "text/html"}, b"jobs"),
        }
    )
    pages = BoundedCrawler(transport=transport, resolver=public_resolver).crawl((first,))
    assert pages[0].url == second
    assert pages[0].redirects == (second,)


def test_crawler_prevents_link_cycles() -> None:
    first = "https://example.com/"
    jobs = "https://example.com/jobs"
    transport = FakeTransport(
        {
            first: HttpResponse(
                200,
                first,
                {"content-type": "text/html"},
                b'<a href="/jobs">Careers</a>',
            ),
            jobs: HttpResponse(
                200,
                jobs,
                {"content-type": "text/html"},
                b'<a href="/">Careers home</a>',
            ),
        }
    )
    pages = BoundedCrawler(transport=transport, resolver=public_resolver).crawl((first,))
    assert len(pages) == 2
    assert transport.calls == [first, jobs]


def test_scrapling_session_stays_active_for_entire_bounded_crawl(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetchers = importlib.import_module("scrapling.fetchers")
    state: dict[str, Any] = {
        "active": False,
        "entered": 0,
        "closed": 0,
        "calls": [],
    }

    class Response:
        status = 200
        headers: ClassVar[dict[str, str]] = {"content-type": "text/html"}

        def __init__(self, url: str, body: bytes) -> None:
            self.url = url
            self._body = body

        @property
        def body(self) -> bytes:
            if not state["active"]:
                raise RuntimeError("No active session available.")
            return self._body

    class ActiveSession:
        def get(self, url: str) -> Response:
            assert state["active"] is True
            state["calls"].append(url)
            if url.endswith("/"):
                return Response(url, b'<a href="/careers">Careers</a>')
            return Response(url, b"Careers")

    class SessionContext:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> ActiveSession:
            state["entered"] += 1
            state["active"] = True
            return ActiveSession()

        def __exit__(self, *_args: object) -> None:
            state["active"] = False
            state["closed"] += 1

    monkeypatch.setattr(fetchers, "FetcherSession", SessionContext)
    crawler = BoundedCrawler(
        transport=network_module.ScraplingTransport(), resolver=public_resolver
    )
    pages = crawler.crawl(("https://example.com/",))
    assert len(pages) == 2
    assert state == {
        "active": False,
        "entered": 1,
        "closed": 1,
        "calls": ["https://example.com/", "https://example.com/careers"],
    }


def test_scrapling_session_closes_after_fetch_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetchers = importlib.import_module("scrapling.fetchers")
    state: dict[str, Any] = {"active": False, "closed": 0}

    class ActiveSession:
        def get(self, _url: str) -> object:
            raise TimeoutError("timed out")

    class SessionContext:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> ActiveSession:
            state["active"] = True
            return ActiveSession()

        def __exit__(self, *_args: object) -> None:
            state["active"] = False
            state["closed"] += 1

    monkeypatch.setattr(fetchers, "FetcherSession", SessionContext)
    crawler = BoundedCrawler(
        transport=network_module.ScraplingTransport(), resolver=public_resolver
    )
    assert crawler.crawl(("https://example.com/",)) == ()
    assert crawler.errors == ["Page request timed out"]
    assert state == {"active": False, "closed": 1}


def test_auto_connects_single_high_confidence_supported_source() -> None:
    run_discovery = service_module().run_discovery

    record = company()
    official = "https://datasentics.com/"
    jobs = "https://datasentics.dream.jobs/jobs"
    outcome = run_discovery(
        company_id=record.pk,
        search_provider=FakeSearch(SearchResult("Data Sentics", official)),
        crawler=FakeCrawler(page(official, links=(jobs,)), page(jobs)),
    )
    assert outcome.status == "connected"
    source = model("companies.CompanySource").objects.get(company=record)
    assert (source.source, source.source_jobs_url, source.is_active) == ("dreamjobs", jobs, True)


def test_manual_data_sentics_domain_bypasses_brave_and_connects_dreamjobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_discovery = service_module().run_discovery
    settings = importlib.import_module("django.conf").settings
    monkeypatch.setattr(settings, "SOURCE_DISCOVERY_BRAVE_API_KEY", "")
    record = company()
    official = "https://datasentics.com/"
    careers_home = "https://careers.datasentics.com/"
    careers = "https://careers.datasentics.com/jobs"
    outcome = run_discovery(
        company_id=record.pk,
        supplied_domain="datasentics.com",
        search_provider=FakeSearch(error=AssertionError("search must be bypassed")),
        crawler=FakeCrawler(
            page(official, "<title>Data Sentics</title>", (careers_home,)),
            page(
                careers_home,
                "<title>Career at Data Sentics</title> __NEXT_DATA__ api.dream.jobs",
                (careers,),
            ),
            page(careers, "__NEXT_DATA__ api.dream.jobs"),
        ),
    )
    run = model("discovery.DiscoveryRun").objects.get(pk=outcome.run_id)
    source = model("companies.CompanySource").objects.get(company=record)
    assert outcome.status == "connected"
    assert run.official_website_url == official
    assert run.careers_url == careers
    assert (source.source, source.source_jobs_url) == ("dreamjobs", careers)


def test_linkedin_search_result_is_rejected_as_official_site() -> None:
    run_discovery = service_module().run_discovery
    record = company("Acuity Analytics")
    linkedin = "https://www.linkedin.com/jobs/search-results/?keywords=acuity"
    outcome = run_discovery(
        company_id=record.pk,
        search_provider=FakeSearch(SearchResult("Acuity Analytics Jobs", linkedin)),
        crawler=FakeCrawler(),
    )
    run = model("discovery.DiscoveryRun").objects.get(pk=outcome.run_id)
    candidate = model("discovery.DiscoveryCandidate").objects.get(run=run, canonical_url=linkedin)
    assert outcome.status == "needs_review"
    assert run.official_website_url is None
    assert candidate.decision == "needs_review"
    assert candidate.official_site_eligibility == "not_official_site"
    assert candidate.job_source_eligibility == "external_job_board"
    assert candidate.job_source_confidence == 82
    assert "LinkedIn" in candidate.reason


def test_manual_linkedin_url_is_rejected_as_official_domain() -> None:
    run_discovery = service_module().run_discovery
    record = company("Acuity Analytics")
    linkedin = "https://www.linkedin.com/jobs/search-results/?keywords=acuity"
    outcome = run_discovery(
        company_id=record.pk,
        supplied_domain=linkedin,
        search_provider=FakeSearch(error=AssertionError("search must be bypassed")),
        crawler=FakeCrawler(),
    )
    run = model("discovery.DiscoveryRun").objects.get(pk=outcome.run_id)
    assert outcome.status == "needs_review"
    assert run.official_website_url is None
    assert not model("companies.CompanySource").objects.filter(company=record).exists()


def test_linkedin_first_result_does_not_hide_real_official_site() -> None:
    run_discovery = service_module().run_discovery
    record = company("Acuity Analytics")
    linkedin = "https://www.linkedin.com/jobs/search-results/?keywords=acuity"
    official = "https://www.acuityanalytics.com/"
    careers = "https://acuityanalytics.applytojob.com/apply"
    outcome = run_discovery(
        company_id=record.pk,
        search_provider=FakeSearch(
            SearchResult("Acuity Analytics Jobs", linkedin),
            SearchResult("Acuity Analytics", official),
        ),
        crawler=FakeCrawler(
            page(official, "<title>Acuity Analytics</title>", (careers,)),
            page(careers),
        ),
    )
    run = model("discovery.DiscoveryRun").objects.get(pk=outcome.run_id)
    assert outcome.status == "connected"
    assert run.official_website_url == official
    assert (
        model("discovery.DiscoveryCandidate")
        .objects.filter(run=run, canonical_url=linkedin, decision="needs_review")
        .exists()
    )


def test_careers_subdomain_search_result_is_not_an_official_site() -> None:
    run_discovery = service_module().run_discovery
    record = company()
    official = "https://datasentics.com/"
    careers = "https://careers.datasentics.com/jobs"
    outcome = run_discovery(
        company_id=record.pk,
        search_provider=FakeSearch(
            SearchResult("Data Sentics", official),
            SearchResult("Data Sentics Careers", careers),
        ),
        crawler=FakeCrawler(
            page(official, "Data Sentics", (careers,)),
            page(careers, "__NEXT_DATA__ api.dream.jobs"),
        ),
    )
    rejected = model("discovery.DiscoveryCandidate").objects.get(
        kind="official_site", canonical_url=careers
    )
    assert outcome.status == "connected"
    assert rejected.decision == "needs_review"
    assert rejected.official_site_eligibility == "not_official_site"
    assert rejected.job_source_eligibility == "possible_job_source"
    assert "not the official corporate website" in rejected.reason


def test_initial_discovery_uses_tavily_then_daily_updates_use_only_saved_adapter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_discovery = service_module().run_discovery
    record = company("Data Sentics")
    official = "https://datasentics.com/"
    careers = "https://careers.datasentics.com/jobs"
    provider = FakeSearch(
        SearchResult("Data Sentics", official, "Data Sentics official website"),
        SearchResult("Data Sentics Careers", careers, "Data Sentics careers jobs"),
    )
    monkeypatch.setattr(
        "discovery.service.configured_search_provider",
        lambda: provider,
    )

    outcome = run_discovery(
        company_id=record.pk,
        crawler=FakeCrawler(
            page(official, "Data Sentics", (careers,)),
            page(careers, "__NEXT_DATA__ api.dream.jobs"),
        ),
    )
    source = model("companies.CompanySource").objects.get(company=record)
    assert outcome.status == "connected"
    assert (source.source, source.source_jobs_url) == ("dreamjobs", careers)
    assert provider.queries == [
        '"Data Sentics" official website',
        '"Data Sentics" official careers jobs',
        '"Data Sentics" Darwinbox careers',
        '"Data Sentics" JazzHR jobs',
        '"Data Sentics" Lever jobs',
    ]

    adapter_calls = 0
    SourceBatch = importlib.import_module("scraping.sources.base").SourceBatch

    class CountingDreamJobsAdapter:
        def fetch(self, *, company: Any) -> Any:
            nonlocal adapter_calls
            del company
            adapter_calls += 1
            return SourceBatch(records=(), requests_made=1)

    def forbidden_tavily() -> object:
        raise AssertionError("Saved-source updates must not initialize Tavily")

    registry = importlib.import_module("scraping.sources.registry")
    monkeypatch.setitem(
        registry._ADAPTER_FACTORIES,
        "dreamjobs",
        CountingDreamJobsAdapter,
    )
    monkeypatch.setattr("discovery.service.configured_search_provider", forbidden_tavily)
    executor_type = importlib.import_module(
        "scraping.background"
    ).ControlledBackgroundExecutor
    with executor_type(max_workers=1) as executor:
        for _index in range(3):
            submission = executor.submit_company(company=record)
            submission.submitted[0].future.result(timeout=5)

    assert adapter_calls == 3
    assert model("discovery.DiscoveryRun").objects.filter(company=record).count() == 1


def test_official_site_403_uses_search_fallback_without_auto_connecting() -> None:
    run_discovery = service_module().run_discovery
    record = company("Acuity Analytics")
    official = "https://www.acuityanalytics.com/"
    careers = "https://www.acuityanalytics.com/careers"
    ats = "https://acuityanalytics.applytojob.com/apply"
    linkedin = "https://www.linkedin.com/jobs/search/?keywords=acuity"

    class QuerySearch:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, query: str, *, limit: int = 5) -> tuple[Any, ...]:
            self.queries.append(query)
            if query == '"Acuity Analytics" official website':
                return (SearchResult("Acuity Analytics", official),)
            if query.startswith("site:"):
                raise search_module.TavilyRateLimitError("Tavily search request limit was reached")
            return (
                SearchResult("Acuity Analytics Careers", careers, "Acuity Analytics jobs"),
                SearchResult("Acuity Analytics Jobs", ats, "Careers at Acuity Analytics"),
                SearchResult("Acuity jobs", linkedin, "Acuity Analytics jobs"),
                SearchResult("Other Company", "https://other.example/jobs", "Unrelated"),
            )[:limit]

    provider = QuerySearch()
    outcome = run_discovery(
        company_id=record.pk,
        search_provider=provider,
        crawler=FakeCrawler(errors=("Page returned HTTP 403",)),
    )
    run = model("discovery.DiscoveryRun").objects.get(pk=outcome.run_id)
    official_candidate = model("discovery.DiscoveryCandidate").objects.get(
        run=run, kind="official_site", canonical_url=official
    )
    source_candidate = model("discovery.DiscoveryCandidate").objects.get(
        run=run, kind="source", canonical_url=ats
    )
    linkedin_candidate = model("discovery.DiscoveryCandidate").objects.get(
        run=run, kind="careers", canonical_url=linkedin
    )
    assert outcome.status == "needs_review"
    assert run.error_code == "TavilyRateLimitError"
    assert run.official_website_url == official
    assert run.careers_url == careers
    assert "HTTP 403" in " ".join(official_candidate.evidence)
    assert official_candidate.decision == "selected"
    assert (source_candidate.platform, source_candidate.decision) == (
        "jazzhr",
        "needs_review",
    )
    assert "JazzHR technical signal" in source_candidate.evidence
    assert 'Fallback query: "Acuity Analytics" official careers jobs' in (
        source_candidate.evidence
    )
    assert not any("site:" in evidence for evidence in source_candidate.evidence)
    assert linkedin_candidate.decision == "needs_review"
    assert linkedin_candidate.official_site_eligibility == "not_official_site"
    assert linkedin_candidate.job_source_eligibility == "external_job_board"
    assert provider.queries == [
        '"Acuity Analytics" official website',
        '"Acuity Analytics" official careers jobs',
        "site:www.acuityanalytics.com careers jobs",
        '"Acuity Analytics" Darwinbox careers',
        '"Acuity Analytics" DreamJobs careers',
        '"Acuity Analytics" Lever jobs',
    ]
    assert not model("companies.CompanySource").objects.filter(company=record).exists()


def test_403_fallback_preserves_existing_acuity_sources() -> None:
    run_discovery = service_module().run_discovery
    record = company("Acuity Analytics")
    official = "https://www.acuityanalytics.com/"
    ats = "https://ascent.applytojob.com/apply"
    darwinbox = model("companies.CompanySource").objects.create(
        company=record,
        source="darwinbox",
        source_jobs_url="https://acuitykp.darwinbox.com/ms/candidate/careers",
        approval_status="approved",
        is_active=True,
    )
    jazzhr = model("companies.CompanySource").objects.create(
        company=record,
        source="jazzhr",
        source_jobs_url=f"{ats}/",
        approval_status="approved",
        is_active=True,
    )
    before = list(
        model("companies.CompanySource")
        .objects.filter(company=record)
        .order_by("pk")
        .values("pk", "source", "source_jobs_url", "approval_status", "is_active")
    )

    class ExistingSourceSearch:
        def search(self, query: str, *, limit: int = 5) -> tuple[Any, ...]:
            del limit
            if query == '"Acuity Analytics" official website':
                return (SearchResult("Acuity Analytics", official),)
            return (SearchResult("Acuity Analytics Jobs", ats, "Acuity Analytics careers"),)

    outcome = run_discovery(
        company_id=record.pk,
        search_provider=ExistingSourceSearch(),
        crawler=FakeCrawler(errors=("Page returned HTTP 403",)),
    )
    after = list(
        model("companies.CompanySource")
        .objects.filter(company=record)
        .order_by("pk")
        .values("pk", "source", "source_jobs_url", "approval_status", "is_active")
    )
    candidate = model("discovery.DiscoveryCandidate").objects.get(
        kind="source", platform="jazzhr"
    )
    assert outcome.status == "already_connected"
    assert before == after
    assert candidate.decision == "already_connected"
    assert candidate.company_source_id == jazzhr.pk
    assert {darwinbox.pk, jazzhr.pk} == set(
        model("companies.CompanySource")
        .objects.filter(company=record)
        .values_list("pk", flat=True)
    )


def test_unconfirmed_official_site_returns_needs_review() -> None:
    run_discovery = service_module().run_discovery
    record = company("Acuity Analytics")
    candidate_url = "https://consulting-example.com/company/acuity"
    outcome = run_discovery(
        company_id=record.pk,
        search_provider=FakeSearch(SearchResult("Acuity Analytics profile", candidate_url)),
        crawler=FakeCrawler(),
    )
    run = model("discovery.DiscoveryRun").objects.get(pk=outcome.run_id)
    assert outcome.status == "needs_review"
    assert run.official_website_url is None


def test_discovery_preserves_existing_acuity_sources() -> None:
    run_discovery = service_module().run_discovery
    record = company("Acuity Analytics")
    existing = model("companies.CompanySource").objects.create(
        company=record,
        source="jazzhr",
        source_jobs_url="https://acuityanalytics.applytojob.com/apply",
        approval_status="approved",
        is_active=False,
    )
    outcome = run_discovery(
        company_id=record.pk,
        search_provider=FakeSearch(
            SearchResult(
                "Acuity Analytics Jobs",
                "https://www.linkedin.com/jobs/search-results/?keywords=acuity",
            )
        ),
        crawler=FakeCrawler(),
    )
    existing.refresh_from_db()
    assert outcome.status == "needs_review"
    assert (existing.approval_status, existing.is_active) == ("approved", False)
    assert model("companies.CompanySource").objects.filter(company=record).count() == 1


def test_repeated_discovery_is_idempotent() -> None:
    run_discovery = service_module().run_discovery

    record = company()
    jobs = "https://jobs.lever.co/datasentics"
    kwargs = {
        "company_id": record.pk,
        "search_provider": FakeSearch(SearchResult("Data Sentics", "https://datasentics.com/")),
        "crawler": FakeCrawler(page("https://datasentics.com/", links=(jobs,)), page(jobs)),
    }
    assert run_discovery(**kwargs).status == "connected"
    assert run_discovery(**kwargs).status == "already_connected"
    assert model("companies.CompanySource").objects.filter(company=record).count() == 1


def test_existing_inactive_approved_source_is_not_reactivated() -> None:
    run_discovery = service_module().run_discovery
    record = company()
    jobs = "https://jobs.lever.co/datasentics"
    source = model("companies.CompanySource").objects.create(
        company=record,
        source="lever",
        source_jobs_url=jobs,
        approval_status="approved",
        is_active=False,
    )
    outcome = run_discovery(
        company_id=record.pk,
        supplied_domain="datasentics.com",
        crawler=FakeCrawler(
            page("https://datasentics.com/", links=(jobs,)),
            page(jobs),
        ),
    )
    source.refresh_from_db()
    assert outcome.status == "already_connected"
    assert source.is_active is False


def test_existing_blocked_source_is_not_approved_automatically() -> None:
    run_discovery = service_module().run_discovery
    record = company()
    jobs = "https://jobs.lever.co/datasentics"
    source = model("companies.CompanySource").objects.create(
        company=record,
        source="lever",
        source_jobs_url=jobs,
        approval_status="blocked",
        is_active=False,
    )
    outcome = run_discovery(
        company_id=record.pk,
        supplied_domain="datasentics.com",
        crawler=FakeCrawler(
            page("https://datasentics.com/", links=(jobs,)),
            page(jobs),
        ),
    )
    source.refresh_from_db()
    candidate = model("discovery.DiscoveryCandidate").objects.get(kind="source")
    assert outcome.status == "needs_review"
    assert candidate.decision == "rejected"
    assert (source.approval_status, source.is_active) == ("blocked", False)


def test_ambiguous_official_domains_require_review() -> None:
    run_discovery = service_module().run_discovery

    record = company("Acme")
    outcome = run_discovery(
        company_id=record.pk,
        search_provider=FakeSearch(
            SearchResult("Acme", "https://acme.com/"),
            SearchResult("Acme Group", "https://acme.org/"),
        ),
        crawler=FakeCrawler(page("https://jobs.lever.co/acme")),
    )
    assert outcome.status == "needs_review"
    run = model("discovery.DiscoveryRun").objects.get(pk=outcome.run_id)
    assert run.official_website_url is None
    assert not model("companies.CompanySource").objects.exists()
    assert model("discovery.DiscoveryCandidate").objects.filter(kind="official_site").count() == 2


def test_unsupported_careers_page_is_persisted_without_source() -> None:
    run_discovery = service_module().run_discovery

    record = company()
    careers = "https://boards.greenhouse.io/datasentics"
    outcome = run_discovery(
        company_id=record.pk,
        supplied_domain="datasentics.com",
        search_provider=FakeSearch(),
        crawler=FakeCrawler(page(careers, "greenhouse.io")),
    )
    assert outcome.status == "unsupported"
    candidate = model("discovery.DiscoveryCandidate").objects.get(kind="source")
    assert candidate.platform == "greenhouse"
    assert not model("companies.CompanySource").objects.exists()


def test_unknown_platform_careers_link_is_persisted() -> None:
    run_discovery = service_module().run_discovery

    record = company()
    careers = "https://datasentics.com/careers"
    outcome = run_discovery(
        company_id=record.pk,
        supplied_domain="datasentics.com",
        crawler=FakeCrawler(page("https://datasentics.com/", links=(careers,))),
    )
    candidate = model("discovery.DiscoveryCandidate").objects.get(kind="careers")
    assert outcome.status == "unsupported"
    assert candidate.platform == ""
    assert "new source adapter" in candidate.reason


def test_empty_search_results_fail_closed_without_fake_source() -> None:
    run_discovery = service_module().run_discovery

    record = company()
    outcome = run_discovery(
        company_id=record.pk, search_provider=FakeSearch(), crawler=FakeCrawler()
    )
    run = model("discovery.DiscoveryRun").objects.get(pk=outcome.run_id)
    assert outcome.status == "failed"
    assert run.error_code == "TavilyEmptyResultsError"
    assert not model("companies.CompanySource").objects.exists()


def test_provider_configuration_failure_is_audited() -> None:
    run_discovery = service_module().run_discovery

    record = company()
    outcome = run_discovery(
        company_id=record.pk,
        search_provider=FakeSearch(error=SearchConfigurationError("missing key")),
        crawler=FakeCrawler(),
    )
    run = model("discovery.DiscoveryRun").objects.get(pk=outcome.run_id)
    assert (outcome.status, run.error_code) == ("failed", "SearchConfigurationError")
    assert run.error_message == "missing key"


def test_configured_search_provider_requires_environment_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = importlib.import_module("django.conf").settings
    monkeypatch.setattr(settings, "SOURCE_DISCOVERY_SEARCH_PROVIDER", "brave")
    monkeypatch.setattr(settings, "SOURCE_DISCOVERY_BRAVE_API_KEY", "")
    with pytest.raises(SearchConfigurationError):
        search_module.configured_search_provider()


@pytest.mark.parametrize(
    ("api_key", "expected_header", "absent_header"),
    [
        ("", ("X-Tavily-Access-Mode", "keyless"), "Authorization"),
        ("tvly-test-secret", ("Authorization", "Bearer tvly-test-secret"), "X-Tavily-Access-Mode"),
    ],
)
def test_tavily_auth_modes_and_result_mapping(
    monkeypatch: pytest.MonkeyPatch,
    api_key: str,
    expected_header: tuple[str, str],
    absent_header: str,
) -> None:
    fetchers = importlib.import_module("scrapling.fetchers")
    state: dict[str, Any] = {"active": False, "closed": 0}

    class Response:
        status = 200
        headers: ClassVar[dict[str, str]] = {"content-type": "application/json"}

        @property
        def body(self) -> bytes:
            assert state["active"] is True
            return (
                b'{"results":[{"title":"Siemens","url":"https://www.siemens.com/",'
                b'"content":"Official Siemens website","score":0.97}]}'
            )

    class Session:
        def post(self, url: str, **kwargs: object) -> Response:
            assert state["active"] is True
            assert url == "https://api.tavily.com/search"
            state["payload"] = kwargs["json"]
            return Response()

    class SessionContext:
        def __init__(self, **kwargs: object) -> None:
            state["headers"] = kwargs["headers"]

        def __enter__(self) -> Session:
            state["active"] = True
            return Session()

        def __exit__(self, *_args: object) -> None:
            state["active"] = False
            state["closed"] += 1

    monkeypatch.setattr(fetchers, "FetcherSession", SessionContext)
    provider = search_module.TavilySearchProvider(
        api_key=api_key,
        allow_keyless=not api_key,
        max_results=6,
    )
    results = provider.search('"Siemens" official website', limit=6)
    headers = state["headers"]
    assert isinstance(headers, dict)
    assert headers[expected_header[0]] == expected_header[1]
    assert absent_header not in headers
    assert state["closed"] == 1
    assert results == (
        SearchResult(
            "Siemens",
            "https://www.siemens.com/",
            "Official Siemens website",
            0.97,
        ),
    )
    assert state["payload"] == {
        "query": '"Siemens" official website',
        "search_depth": "basic",
        "max_results": 6,
        "include_answer": False,
        "include_raw_content": False,
        "include_images": False,
    }


def test_tavily_rate_limit_fails_closed_and_closes_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fetchers = importlib.import_module("scrapling.fetchers")
    state = {"closed": 0}

    class Response:
        status = 429
        headers: ClassVar[dict[str, str]] = {}
        body = b'{"detail":"limited"}'

    class Session:
        def post(self, _url: str, **_kwargs: object) -> Response:
            return Response()

    class SessionContext:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> Session:
            return Session()

        def __exit__(self, *_args: object) -> None:
            state["closed"] += 1

    monkeypatch.setattr(fetchers, "FetcherSession", SessionContext)
    provider = search_module.TavilySearchProvider(allow_keyless=True)
    with pytest.raises(search_module.TavilyRateLimitError):
        provider.search("Siemens official website")
    assert state["closed"] == 1


def test_tavily_production_mode_requires_its_own_key_but_not_brave(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = importlib.import_module("django.conf").settings
    monkeypatch.setattr(settings, "SOURCE_DISCOVERY_SEARCH_PROVIDER", "tavily")
    monkeypatch.setattr(settings, "SOURCE_DISCOVERY_TAVILY_API_KEY", "")
    monkeypatch.setattr(settings, "SOURCE_DISCOVERY_TAVILY_KEYLESS_DIAGNOSTIC", False)
    monkeypatch.setattr(settings, "SOURCE_DISCOVERY_BRAVE_API_KEY", "")
    with pytest.raises(SearchConfigurationError):
        search_module.configured_search_provider()


def test_tavily_keyless_requires_explicit_diagnostic_opt_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = importlib.import_module("django.conf").settings
    monkeypatch.setattr(settings, "SOURCE_DISCOVERY_SEARCH_PROVIDER", "tavily")
    monkeypatch.setattr(settings, "SOURCE_DISCOVERY_TAVILY_API_KEY", "")
    monkeypatch.setattr(settings, "SOURCE_DISCOVERY_TAVILY_KEYLESS_DIAGNOSTIC", True)
    monkeypatch.setattr(settings, "SOURCE_DISCOVERY_BRAVE_API_KEY", "")
    provider = search_module.configured_search_provider()
    assert isinstance(provider, search_module.TavilySearchProvider)
    assert provider._headers()["X-Tavily-Access-Mode"] == "keyless"


def test_discovery_searches_by_company_name_without_manual_domain() -> None:
    run_discovery = service_module().run_discovery
    record = company("Siemens")
    provider = FakeSearch(SearchResult("Siemens", "https://www.siemens.com/"))
    outcome = run_discovery(
        company_id=record.pk,
        search_provider=provider,
        crawler=FakeCrawler(page("https://www.siemens.com/", "Siemens")),
    )
    run = model("discovery.DiscoveryRun").objects.get(pk=outcome.run_id)
    assert provider.queries == [
        '"Siemens" official website',
        '"Siemens" official careers jobs',
        '"Siemens" Darwinbox careers',
        '"Siemens" DreamJobs careers',
        '"Siemens" JazzHR jobs',
        '"Siemens" Lever jobs',
    ]
    assert run.supplied_domain == ""
    assert run.official_website_url == "https://www.siemens.com/"


def test_registry_driven_sweep_finds_second_platform_and_records_coverage() -> None:
    run_discovery = service_module().run_discovery
    record = company("Two Source Company")
    official = "https://twosource.example/"
    lever = "https://jobs.lever.co/two-source-company"
    jazzhr = "https://two-source-company.applytojob.com/apply"

    class PlatformSearch:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, query: str, *, limit: int = 5) -> tuple[Any, ...]:
            self.queries.append(query)
            if query == '"Two Source Company" official website':
                return (SearchResult("Two Source Company", official),)
            if query == '"Two Source Company" official careers jobs':
                return (
                    SearchResult(
                        "Two Source Company jobs",
                        lever,
                        "Careers at Two Source Company",
                    ),
                )
            if "JazzHR" in query:
                return (
                    SearchResult(
                        "Two Source Company careers",
                        jazzhr,
                        "Two Source Company jobs",
                    ),
                )
            return ()

    provider = PlatformSearch()
    outcome = run_discovery(
        company_id=record.pk,
        search_provider=provider,
        crawler=FakeCrawler(page(official, "Two Source Company")),
    )
    run = model("discovery.DiscoveryRun").objects.get(pk=outcome.run_id)
    candidates = list(
        run.candidates.filter(kind="source").order_by("platform").values_list(
            "platform", "canonical_url", "origin"
        )
    )
    checks = dict(run.adapter_checks.values_list("platform", "status"))
    presentations = importlib.import_module(
        "discovery.presentation"
    ).latest_candidate_presentations(run)

    assert outcome.status == "needs_review"
    assert candidates == [
        ("jazzhr", jazzhr, "adapter_search"),
        ("lever", lever, "current_discovery"),
    ]
    assert checks == {
        "darwinbox": "not_found",
        "dreamjobs": "not_found",
        "jazzhr": "found",
        "lever": "found",
    }
    assert record.sources.count() == 0
    assert len(provider.queries) == 5
    assert {
        item.candidate.platform for item in presentations if item.can_connect
    } == {"jazzhr", "lever"}


def test_new_acuity_discovers_jazzhr_and_embedded_darwinbox_source() -> None:
    run_discovery = service_module().run_discovery
    confirm_candidate = service_module().confirm_candidate
    official = "https://www.acuityanalytics.com/"
    jazzhr_url = "https://ascent.applytojob.com/apply"
    darwinbox_url = "https://acuitykp.darwinbox.com/ms/candidate/careers"

    deleted_company = company("Acuity Analytics")
    deleted_company.sources.create(
        source="darwinbox",
        source_jobs_url=darwinbox_url,
        approval_status="approved",
        is_active=True,
    )
    deleted_company.delete()
    record = company("Acuity Analytics")

    class AcuitySearch:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, query: str, *, limit: int = 5) -> tuple[Any, ...]:
            del limit
            self.queries.append(query)
            if query == '"Acuity Analytics" official website':
                return (SearchResult("Acuity Analytics", official),)
            if query in {
                '"Acuity Analytics" official careers jobs',
                "site:www.acuityanalytics.com careers jobs",
            }:
                return (
                    SearchResult(
                        "Acuity Analytics careers",
                        jazzhr_url,
                        "Acuity Analytics jobs and careers.",
                    ),
                )
            if query == '"Acuity Analytics" Darwinbox careers':
                return (
                    SearchResult(
                        "Acuity Knowledge Partners | Customer Success Story",
                        "https://explore.darwinbox.com/lp/resources/casestudy/acuity",
                        "Acuity Knowledge Partners uses Darwinbox.",
                    ),
                )
            if query == '"Acuity Knowledge Partners" "/ms/candidate/careers"':
                return ()
            return ()

    class AcuityCrawler:
        errors = ("Page returned HTTP 403",)

        def crawl(self, seeds: tuple[str, ...]) -> tuple[Any, ...]:
            if seeds == (darwinbox_url,):
                return (page(darwinbox_url, "Darwinbox careers"),)
            return ()

    provider = AcuitySearch()
    first = run_discovery(
        company_id=record.pk,
        search_provider=provider,
        crawler=AcuityCrawler(),
    )
    first_run = model("discovery.DiscoveryRun").objects.get(pk=first.run_id)
    first_candidates = {
        candidate.platform: candidate
        for candidate in first_run.candidates.filter(kind="source")
    }
    inventory = importlib.import_module(
        "discovery.presentation"
    ).company_candidate_presentations(company_id=record.pk)

    assert first.status == "needs_review"
    assert set(first_candidates) == {"darwinbox", "jazzhr"}
    assert first_candidates["darwinbox"].canonical_url == darwinbox_url
    assert first_candidates["jazzhr"].canonical_url == jazzhr_url
    assert dict(first_run.adapter_checks.values_list("platform", "status")) == {
        "darwinbox": "found",
        "dreamjobs": "not_found",
        "jazzhr": "found",
        "lever": "not_checked",
    }
    assert {item.candidate.platform for item in inventory if item.can_confirm} == {
        "darwinbox",
        "jazzhr",
    }

    confirm_candidate(
        candidate_id=first_candidates["jazzhr"].pk,
        company_id=record.pk,
    )
    remaining = importlib.import_module(
        "discovery.presentation"
    ).company_candidate_presentations(company_id=record.pk)
    assert "darwinbox" in {item.candidate.platform for item in remaining}

    confirm_candidate(
        candidate_id=first_candidates["darwinbox"].pk,
        company_id=record.pk,
    )
    assert list(
        record.sources.order_by("source").values_list("source", "source_jobs_url")
    ) == [("darwinbox", darwinbox_url), ("jazzhr", jazzhr_url)]

    second = run_discovery(
        company_id=record.pk,
        search_provider=AcuitySearch(),
        crawler=AcuityCrawler(),
    )
    second_run = model("discovery.DiscoveryRun").objects.get(pk=second.run_id)
    assert record.sources.count() == 2
    assert set(
        second_run.candidates.filter(kind="source").values_list(
            "platform", flat=True
        )
    ) == {"darwinbox", "jazzhr"}


def test_darwinbox_partial_url_in_search_evidence_uses_registered_careers_route() -> None:
    result = SearchResult(
        "Acuity Knowledge Partners campus hiring",
        "https://www.instagram.com/p/public-hiring/",
        (
            "Acuity Knowledge Partners apply here: "
            "https://acuitykp.darwinbox.com/ms/candidate/"
        ),
    )
    hint = detectors_module.discovery_hints_for("darwinbox")

    assert service_module()._embedded_source_urls(result, hint) == (
        "https://acuitykp.darwinbox.com/ms/candidate/careers",
    )


def test_existing_acuity_inventory_is_complete_and_idempotent_after_403() -> None:
    run_discovery = service_module().run_discovery
    record = company("Acuity Analytics")
    official = "https://www.acuityanalytics.com/"
    darwinbox_url = "https://acuitykp.darwinbox.com/ms/candidate/careers"
    jazzhr_url = "https://ascent.applytojob.com/apply"
    darwinbox = record.sources.create(
        source="darwinbox",
        source_jobs_url=darwinbox_url,
        approval_status="approved",
        is_active=True,
    )
    jazzhr = record.sources.create(
        source="jazzhr",
        source_jobs_url=jazzhr_url,
        approval_status="approved",
        is_active=True,
    )
    original = list(
        record.sources.order_by("pk").values(
            "pk", "source", "source_jobs_url", "approval_status", "is_active"
        )
    )

    class AcuitySearch:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def search(self, query: str, *, limit: int = 5) -> tuple[Any, ...]:
            del limit
            self.queries.append(query)
            if query == '"Acuity Analytics" official website':
                return (SearchResult("Acuity Analytics", official),)
            if "official careers" in query or query.startswith("site:"):
                return (
                    SearchResult(
                        "Acuity Analytics jobs",
                        jazzhr_url,
                        "Careers at Acuity Analytics",
                    ),
                )
            return ()

    provider = AcuitySearch()
    outcome = run_discovery(
        company_id=record.pk,
        search_provider=provider,
        crawler=FakeCrawler(errors=("Page returned HTTP 403",)),
    )
    run = model("discovery.DiscoveryRun").objects.get(pk=outcome.run_id)

    assert outcome.status == "already_connected"
    assert list(
        record.sources.order_by("pk").values(
            "pk", "source", "source_jobs_url", "approval_status", "is_active"
        )
    ) == original
    assert list(
        run.candidates.filter(kind="source").order_by("platform").values_list(
            "platform", "company_source_id", "decision"
        )
    ) == [
        ("darwinbox", darwinbox.pk, "already_connected"),
        ("jazzhr", jazzhr.pk, "already_connected"),
    ]
    assert dict(run.adapter_checks.values_list("platform", "status")) == {
        "darwinbox": "already_connected",
        "dreamjobs": "not_found",
        "jazzhr": "already_connected",
        "lever": "not_found",
    }
    assert not any("Darwinbox" in query or "JazzHR" in query for query in provider.queries)


def test_previous_validated_candidate_remains_visible_on_discover_again() -> None:
    run_discovery = service_module().run_discovery
    record = company("Retained Candidate")
    official = "https://retained.example/"
    lever = "https://jobs.lever.co/retained-candidate"

    first = FakeSearch(
        SearchResult("Retained Candidate", official),
        SearchResult("Retained Candidate jobs", lever, "Retained Candidate careers"),
    )
    first_outcome = run_discovery(
        company_id=record.pk,
        search_provider=first,
        crawler=FakeCrawler(page(official, "Retained Candidate")),
    )
    assert model("discovery.DiscoveryCandidate").objects.filter(
        run_id=first_outcome.run_id,
        kind="source",
        canonical_url=lever,
    ).exists()

    second = FakeSearch(SearchResult("Retained Candidate", official))
    second_outcome = run_discovery(
        company_id=record.pk,
        search_provider=second,
        crawler=FakeCrawler(page(official, "Retained Candidate")),
    )
    retained = model("discovery.DiscoveryCandidate").objects.get(
        run_id=second_outcome.run_id,
        kind="source",
        canonical_url=lever,
    )
    assert retained.origin == "previous_discovery"
    assert model("companies.CompanySource").objects.filter(company=record).count() == 0


def test_query_limit_records_partial_adapter_coverage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_discovery = service_module().run_discovery
    record = company("Bounded Company")
    official = "https://bounded.example/"
    settings = importlib.import_module("django.conf").settings
    monkeypatch.setattr(settings, "SOURCE_DISCOVERY_SEARCH_MAX_QUERIES", 3)

    outcome = run_discovery(
        company_id=record.pk,
        search_provider=FakeSearch(SearchResult("Bounded Company", official)),
        crawler=FakeCrawler(page(official, "Bounded Company")),
    )
    run = model("discovery.DiscoveryRun").objects.get(pk=outcome.run_id)

    assert outcome.status == "needs_review"
    assert run.summary.startswith(
        "Partial discovery — some registered platforms were not checked."
    )
    assert run.adapter_checks.filter(status="not_checked").count() == 3


def test_manual_domain_fallback_does_not_call_search() -> None:
    run_discovery = service_module().run_discovery

    record = company()
    outcome = run_discovery(
        company_id=record.pk,
        supplied_domain="datasentics.com",
        search_provider=FakeSearch(error=AssertionError("must not search")),
        crawler=FakeCrawler(),
    )
    assert outcome.status == "not_found"


def test_manual_confirmation_connects_review_candidate() -> None:
    confirm_candidate = service_module().confirm_candidate

    record = company("Acme")
    run = model("discovery.DiscoveryRun").objects.create(
        company=record, query="Acme", status="needs_review"
    )
    candidate = model("discovery.DiscoveryCandidate").objects.create(
        run=run,
        kind="source",
        discovered_url="https://jobs.lever.co/acme",
        canonical_url="https://jobs.lever.co/acme",
        platform="lever",
        confidence=85,
        evidence=["jobs.lever.co host"],
        supported=True,
        decision="needs_review",
    )
    confirmed = confirm_candidate(candidate_id=candidate.pk, company_id=record.pk)
    assert confirmed.decision == "connected"
    assert confirmed.company_source.is_active is True


def test_manual_confirmation_rejects_unsupported_candidate() -> None:
    confirm_candidate = service_module().confirm_candidate

    record = company("Acme")
    run = model("discovery.DiscoveryRun").objects.create(company=record, query="Acme")
    candidate = model("discovery.DiscoveryCandidate").objects.create(
        run=run,
        kind="source",
        discovered_url="https://example.com/jobs",
        canonical_url="https://example.com/jobs",
        platform="unknown",
        confidence=50,
        supported=False,
        decision="unsupported",
    )
    with pytest.raises(ValueError):
        confirm_candidate(candidate_id=candidate.pk, company_id=record.pk)


def test_weak_supported_signal_never_auto_connects() -> None:
    run_discovery = service_module().run_discovery

    record = company()
    jobs = "https://datasentics.com/jobs"
    outcome = run_discovery(
        company_id=record.pk,
        supplied_domain="datasentics.com",
        crawler=FakeCrawler(page(jobs, "api.dream.jobs")),
    )
    candidate = model("discovery.DiscoveryCandidate").objects.get(kind="source")
    assert (outcome.status, candidate.confidence) == ("needs_review", 88)
    assert not model("companies.CompanySource").objects.exists()


def test_multiple_distinct_supported_sources_are_preserved_for_review() -> None:
    run_discovery = service_module().run_discovery

    record = company()
    lever = "https://jobs.lever.co/datasentics"
    jazzhr = "https://datasentics.applytojob.com/apply"
    outcome = run_discovery(
        company_id=record.pk,
        supplied_domain="datasentics.com",
        crawler=FakeCrawler(page("https://datasentics.com/", links=(lever, jazzhr))),
    )
    candidates = model("discovery.DiscoveryCandidate").objects.filter(kind="source")
    assert outcome.status == "needs_review"
    assert set(candidates.values_list("platform", flat=True)) == {"lever", "jazzhr"}


def test_detector_support_comes_from_source_registry() -> None:
    registered_source_keys = importlib.import_module(
        "scraping.sources.registry"
    ).registered_source_keys

    assert {"lever", "darwinbox", "jazzhr", "dreamjobs"} <= set(registered_source_keys())
    assert detect_page(page("https://jobs.lever.co/acme"))[0].supported is True


def test_company_ui_shows_discovery_and_queues_background(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = importlib.import_module("django.conf").settings
    Client = importlib.import_module("django.test").Client
    reverse = importlib.import_module("django.urls").reverse

    record = company()
    monkeypatch.setattr(settings, "ALLOWED_HOSTS", ["testserver"])
    calls: list[tuple[int, str]] = []

    def submit_discovery(*, company: Any, supplied_domain: str = "") -> object:
        calls.append((company.pk, supplied_domain))
        return object()

    monkeypatch.setattr("discovery.views.background_executor.submit_discovery", submit_discovery)
    web = Client()
    response = web.get(reverse("companies:detail", args=(record.pk,)))
    assert b"Discover sources" in response.content
    response = web.post(
        reverse("discovery:start", args=(record.pk,)), {"domain": "datasentics.com"}
    )
    assert response.status_code == 302
    assert calls == [(record.pk, "datasentics.com")]


def test_shared_background_executor_rejects_duplicate_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    background = importlib.import_module("scraping.background")
    executor = background.ControlledBackgroundExecutor(max_workers=1)
    record = company()
    started = Event()
    release = Event()

    def worker(*, company_id: int, supplied_domain: str) -> object:
        assert (company_id, supplied_domain) == (record.pk, "datasentics.com")
        started.set()
        release.wait(timeout=5)
        return object()

    monkeypatch.setattr(executor, "_run_discovery", worker)
    try:
        future = executor.submit_discovery(company=record, supplied_domain="datasentics.com")
        assert started.wait(timeout=2)
        with pytest.raises(background.BackgroundRunAlreadyScheduledError):
            executor.submit_discovery(company=record)
        release.set()
        future.result(timeout=2)
    finally:
        release.set()
        executor.shutdown(wait=True)


def test_real_scrapling_background_path_materializes_responses_inside_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Exercise installed Scrapling's real session/_make_request/background path offline."""
    background = importlib.import_module("scraping.background")
    scrapling_static = importlib.import_module("scrapling.engines.static")
    record = company()
    official = "https://datasentics.com/"
    careers_home = "https://careers.datasentics.com/"
    jobs = "https://careers.datasentics.com/jobs"
    bodies = {
        official: b'<title>Data Sentics</title><a href="https://careers.datasentics.com/">Career</a>',
        careers_home: (
            b'<title>Career at Data Sentics</title><a href="/jobs">Show jobs</a>'
            b'<script id="__NEXT_DATA__"></script>api.dream.jobs'
        ),
        jobs: b'<script id="__NEXT_DATA__"></script>api.dream.jobs',
    }
    request_calls: list[str] = []

    def request(_session: object, method: str, **kwargs: object) -> object:
        url = str(kwargs["url"])
        request_calls.append(url)
        if url == search_module.TavilySearchProvider.endpoint:
            headers = {
                str(key).lower(): str(value)
                for key, value in cast(dict[object, object], kwargs["headers"]).items()
            }
            assert (method, headers.get("x-tavily-access-mode")) == ("POST", "keyless")
            content = (
                b'{"results":['
                b'{"title":"Data Sentics","url":"https://datasentics.com/",'
                b'"content":"Data Sentics official website","score":0.99},'
                b'{"title":"Data Sentics Careers",'
                b'"url":"https://careers.datasentics.com/jobs",'
                b'"content":"Data Sentics jobs","score":0.95}'
                b']}'
            )
        else:
            content = bodies[url]
        return SimpleNamespace(
            url=url,
            content=content,
            status_code=200,
            reason="OK",
            encoding="utf-8",
            cookies={},
            headers={"content-type": "text/html"},
            request=SimpleNamespace(headers={}, method=method),
            history=[],
        )

    def getaddrinfo(*_args: object, **_kwargs: object) -> list[tuple[object, ...]]:
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    monkeypatch.setattr(scrapling_static.CurlSession, "request", request)
    monkeypatch.setattr("discovery.network.socket.getaddrinfo", getaddrinfo)
    settings = importlib.import_module("django.conf").settings
    monkeypatch.setattr(settings, "SOURCE_DISCOVERY_TAVILY_KEYLESS_DIAGNOSTIC", True)
    executor = background.ControlledBackgroundExecutor(max_workers=1)
    try:
        outcome = executor.submit_discovery(
            company=record, supplied_domain=""
        ).result(timeout=5)
    finally:
        executor.shutdown(wait=True)
    run = model("discovery.DiscoveryRun").objects.get(pk=outcome.run_id)
    source = model("companies.CompanySource").objects.get(company=record)
    assert (run.status, run.error_message) == ("connected", "")
    assert run.careers_url == jobs
    assert (source.source, source.source_jobs_url) == ("dreamjobs", jobs)
    assert request_calls.count(search_module.TavilySearchProvider.endpoint) == 5
    assert [url for url in request_calls if url != search_module.TavilySearchProvider.endpoint] == [
        official,
        careers_home,
        jobs,
    ]
